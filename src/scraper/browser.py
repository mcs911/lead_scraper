"""Stealth-configured Playwright session with rate limiting and retries."""

from __future__ import annotations

import asyncio
import logging
import random
from types import TracebackType
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.scraper.config import ScrapeConfig
from src.scraper.stealth import (
    DEFAULT_HEADERS,
    LAUNCH_ARGS,
    STEALTH_JS,
    pick_user_agent,
)

logger = logging.getLogger(__name__)

#: Status codes worth retrying: rate limiting and transient server faults.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Resource types dropped to cut bandwidth and page load time. Stylesheets are
#: kept: blocking them changes layout enough that some sites treat it as a bot
#: signal, and the saving is small next to images and media.
BLOCKED_RESOURCES = frozenset({"image", "media", "font"})


class FetchError(RuntimeError):
    """Raised when a URL could not be fetched after exhausting retries."""


class StealthBrowser:
    """Async context manager owning a stealth-patched Chromium context.

    Concurrency is capped by a semaphore and every navigation is preceded by a
    randomised delay, so callers can fan out freely without having to think
    about request pacing::

        async with StealthBrowser(config) as browser:
            html = await browser.fetch("https://example.com/")
    """

    def __init__(self, config: ScrapeConfig | None = None) -> None:
        self.config = config or ScrapeConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._semaphore = asyncio.Semaphore(self.config.concurrency)

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> StealthBrowser:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Launch Chromium and open a stealth-patched browsing context."""
        cfg = self.config
        self._playwright = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "headless": cfg.headless,
            "args": list(LAUNCH_ARGS),
        }
        if cfg.executable_path:
            launch_kwargs["executable_path"] = cfg.executable_path
        if cfg.proxy:
            launch_kwargs["proxy"] = {"server": cfg.proxy}

        try:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except PlaywrightError:
            # A pinned executable_path that no longer exists should not be
            # fatal — fall back to Playwright's own managed browser.
            if not cfg.executable_path:
                raise
            logger.warning(
                "Chromium not found at %s, falling back to the Playwright-managed "
                "browser",
                cfg.executable_path,
            )
            launch_kwargs.pop("executable_path")
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        width, height = cfg.viewport
        self._context = await self._browser.new_context(
            user_agent=pick_user_agent(cfg.user_agent),
            locale=cfg.locale,
            timezone_id=cfg.timezone_id,
            viewport={"width": width, "height": height},
            extra_http_headers={**DEFAULT_HEADERS, **cfg.extra_headers},
            java_script_enabled=True,
        )
        self._context.set_default_timeout(cfg.timeout_ms)
        await self._context.add_init_script(STEALTH_JS)
        await self._context.route("**/*", self._route_filter)
        logger.debug("Browser context ready (concurrency=%s)", cfg.concurrency)

    async def close(self) -> None:
        """Tear down the context, browser, and Playwright driver."""
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except PlaywrightError:  # pragma: no cover - best-effort cleanup
                    logger.debug("Error closing browser resource", exc_info=True)
        if self._playwright is not None:
            await self._playwright.stop()
        self._context = self._browser = self._playwright = None

    @staticmethod
    async def _route_filter(route: Any, request: Any) -> None:
        """Abort heavyweight subresources the scraper never reads."""
        if request.resource_type in BLOCKED_RESOURCES:
            await route.abort()
        else:
            await route.continue_()

    # -- fetching ----------------------------------------------------------

    async def fetch(self, url: str, *, wait_for: str | None = None) -> str:
        """Return the rendered HTML of ``url``.

        Retries on timeouts and on the status codes in :data:`RETRYABLE_STATUS`
        with exponential backoff plus jitter. ``wait_for`` is an optional CSS
        selector to await before reading the DOM, for content injected after
        load.

        Raises:
            FetchError: every attempt failed.
        """
        if self._context is None:
            raise RuntimeError("StealthBrowser.start() must be called first")

        cfg = self.config
        last_error: str = "unknown error"

        for attempt in range(1, cfg.max_retries + 1):
            async with self._semaphore:
                await self._pause()
                page = await self._context.new_page()
                try:
                    response: Response | None = await page.goto(
                        url, wait_until="domcontentloaded", timeout=cfg.timeout_ms
                    )
                    status = response.status if response else 0

                    if status and status in RETRYABLE_STATUS:
                        last_error = f"HTTP {status}"
                        logger.warning(
                            "%s for %s (attempt %s/%s)",
                            last_error,
                            url,
                            attempt,
                            cfg.max_retries,
                        )
                        await self._backoff(attempt)
                        continue

                    if status and status >= 400:
                        # 404/403 will not improve on retry.
                        raise FetchError(f"HTTP {status} for {url}")

                    if wait_for:
                        try:
                            await page.wait_for_selector(
                                wait_for, timeout=cfg.timeout_ms
                            )
                        except PlaywrightTimeoutError:
                            # The page loaded; the selector guess may simply be
                            # wrong. Return what rendered and let parsing decide.
                            logger.debug(
                                "wait_for selector %r never appeared on %s",
                                wait_for,
                                url,
                            )

                    return await page.content()

                except PlaywrightTimeoutError:
                    last_error = "timeout"
                    logger.warning(
                        "Timeout for %s (attempt %s/%s)",
                        url,
                        attempt,
                        cfg.max_retries,
                    )
                    await self._backoff(attempt)
                except PlaywrightError as exc:
                    last_error = str(exc).splitlines()[0]
                    logger.warning(
                        "Navigation error for %s (attempt %s/%s): %s",
                        url,
                        attempt,
                        cfg.max_retries,
                        last_error,
                    )
                    await self._backoff(attempt)
                finally:
                    await page.close()

        raise FetchError(
            f"Failed to fetch {url} after {cfg.max_retries} attempts: {last_error}"
        )

    # -- pacing ------------------------------------------------------------

    async def _pause(self) -> None:
        """Sleep a randomised inter-request delay."""
        delay = random.uniform(self.config.min_delay, self.config.max_delay)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _backoff(self, attempt: int) -> None:
        """Sleep an exponentially growing, jittered delay before a retry."""
        base = min(2.0**attempt, 30.0)
        await asyncio.sleep(base + random.uniform(0, 1.0))

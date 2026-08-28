"""Anti-fingerprinting patches for Playwright's Chromium.

Headless Chromium advertises itself in a handful of ways that bot detection
reads directly: ``navigator.webdriver`` is ``true``, the plugin and mimeType
arrays are empty, ``window.chrome`` is missing, and the WebGL strings report
SwiftShader instead of a real GPU. :data:`STEALTH_JS` normalises each of those
to what a regular desktop Chrome reports.

This is a self-contained implementation of what the ``playwright-stealth``
package does. It is inlined rather than taken as a dependency because that
package lags Playwright releases and pulls in a patch set far wider than this
scraper needs.
"""

from __future__ import annotations

import random

from src.scraper.config import USER_AGENTS

#: Injected via ``add_init_script`` so it runs before any page script, in every
#: frame including iframes.
STEALTH_JS = """
(() => {
  // --- navigator.webdriver ------------------------------------------------
  // The single strongest headless signal. Note the target is `false`, not
  // `undefined`: the property is part of the WebDriver spec and a genuine
  // Chrome exposes it returning false, so deleting it outright is itself
  // anomalous. Patch the prototype accessor to keep `'webdriver' in navigator`
  // true while the value reads as a non-automated browser.
  Object.defineProperty(Object.getPrototypeOf(navigator), 'webdriver', {
    get: () => false,
    configurable: true,
  });

  // --- window.chrome ------------------------------------------------------
  // Present in real Chrome, absent in headless.
  if (!window.chrome) {
    Object.defineProperty(window, 'chrome', {
      value: {
        runtime: {},
        loadTimes: function () {},
        csi: function () {},
        app: { isInstalled: false },
      },
      configurable: true,
      writable: true,
    });
  }

  // --- languages ----------------------------------------------------------
  Object.defineProperty(navigator, 'languages', {
    get: () => ['hu-HU', 'hu', 'en-US', 'en'],
    configurable: true,
  });

  // --- plugins & mimeTypes ------------------------------------------------
  // Headless reports zero of each. Build objects that keep the real
  // PluginArray shape so `instanceof` and index access behave.
  const pluginData = [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format' },
    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format' },
    { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format' },
    { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format' },
  ];

  const makePlugins = () => {
    const arr = pluginData.map((p) => {
      const plugin = Object.create(Plugin.prototype);
      Object.defineProperties(plugin, {
        name: { value: p.name, enumerable: true },
        filename: { value: p.filename, enumerable: true },
        description: { value: p.description, enumerable: true },
        length: { value: 1, enumerable: true },
      });
      return plugin;
    });
    const list = Object.create(PluginArray.prototype);
    arr.forEach((p, i) => Object.defineProperty(list, i, {
      value: p, enumerable: true,
    }));
    Object.defineProperty(list, 'length', { value: arr.length });
    Object.defineProperty(list, 'item', { value: (i) => arr[i] ?? null });
    Object.defineProperty(list, 'namedItem', {
      value: (n) => arr.find((p) => p.name === n) ?? null,
    });
    return list;
  };

  Object.defineProperty(navigator, 'plugins', {
    get: makePlugins,
    configurable: true,
  });

  // --- permissions --------------------------------------------------------
  // Headless resolves notifications to 'denied' while Notification.permission
  // says 'default'; that mismatch is a known tell.
  if (window.navigator.permissions) {
    const originalQuery = window.navigator.permissions.query.bind(
      window.navigator.permissions
    );
    window.navigator.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission, onchange: null })
        : originalQuery(parameters);
  }

  // --- hardware -----------------------------------------------------------
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8, configurable: true,
  });
  Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8, configurable: true,
  });
  Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 0, configurable: true,
  });

  // --- WebGL vendor / renderer -------------------------------------------
  // Headless reports "Google Inc." / "SwiftShader". Report a common GPU.
  const patchWebGL = (proto) => {
    if (!proto) return;
    const getParameter = proto.getParameter;
    proto.getParameter = function (parameter) {
      if (parameter === 37445) return 'Intel Inc.';           // UNMASKED_VENDOR
      if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // RENDERER
      return getParameter.apply(this, [parameter]);
    };
  };
  patchWebGL(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchWebGL(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);

  // --- toString hygiene ---------------------------------------------------
  // A patched function whose toString() shows JS source instead of
  // "[native code]" is itself a signal. Route the spoofed ones back through a
  // native-looking toString.
  const nativeToString = Function.prototype.toString;
  const patched = new WeakSet();
  [
    window.navigator.permissions && window.navigator.permissions.query,
    window.WebGLRenderingContext && WebGLRenderingContext.prototype.getParameter,
  ].filter(Boolean).forEach((fn) => patched.add(fn));

  Function.prototype.toString = function () {
    if (patched.has(this)) return 'function () { [native code] }';
    return nativeToString.call(this);
  };
})();
"""

#: Chromium flags. ``AutomationControlled`` is the flag that otherwise makes
#: ``navigator.webdriver`` true before any script runs.
LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--window-size=1920,1080",
)

#: Header set a real Chrome sends that Playwright omits by default.
DEFAULT_HEADERS: dict[str, str] = {
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}


def pick_user_agent(pinned: str | None = None) -> str:
    """Return ``pinned`` if given, otherwise a random desktop Chrome UA."""
    return pinned or random.choice(USER_AGENTS)

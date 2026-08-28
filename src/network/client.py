import logging

import requests

logger = logging.getLogger(__name__)


def fetch_page(url: str) -> str:
    ## For faster scrape switch timeout to 1-5 seconds
    response = requests.get(url, timeout=10)

    logger.debug("HTTP %s", response.status_code)
    logger.debug("Content-Type: %s", response.headers.get("Content-Type"))
    logger.debug("Content-Length: %s", len(response.content))
    response.raise_for_status()  # Raise an error for bad responses

    return response.text

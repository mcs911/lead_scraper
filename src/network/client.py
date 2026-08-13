import requests


def fetch_page(url: str) -> str:
    ## For faster scrape switch timeout to 1-5 seconds
    response = requests.get(url, timeout=10)

    print(f"HTTP{response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    print(f"Content-Length: {len(response.content)}")
    response.raise_for_status()  # Raise an error for bad responses

    return response.text

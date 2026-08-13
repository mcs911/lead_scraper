from src.network.client import fetch_page


def test_fetch_page():
    html = fetch_page("https://example.com")

    assert html
    assert "<html" in html.lower()

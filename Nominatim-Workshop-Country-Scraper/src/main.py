import csv
from pathlib import Path
from typing import List, Dict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SEARCH_URL = "https://nominatim.openstreetmap.org/ui/search.html?q=Workshop&countrycodes=ca&limit=50"
OUT_CSV = Path("output/Workshop&country.csv")

# Toggle for debugging:
DEBUG_HEADED = False  # set True to see the browser during debugging


def _block_unnecessary_requests(page):
    """
    Abort heavy or unnecessary requests to speed up rendering and reduce timeouts.
    Disables heavy resources (map tiles, images, fonts, media) so the page renders faster.
    """
    BLOCK_HOST_PATTERNS = [
        "tile.openstreetmap.org",
        "tiles.openstreetmap.org",
        "basemaps.cartocdn.com",
        "cartocdn.com",
        "fastly.net",
        "gstatic.com",
        "googleapis.com",
    ]
    BLOCK_RESOURCE_TYPES = {"image", "font", "media"}

    def should_block(url: str) -> bool:
        return any(h in url for h in BLOCK_HOST_PATTERNS)

    page.route("**/*", lambda route: route.abort()
               if route.request.resource_type in BLOCK_RESOURCE_TYPES or should_block(route.request.url)
               else route.continue_())


def parse_cards(page) -> List[Dict[str, str]]:
    """
    Extracts cards once they are rendered in the DOM.
    """
    results: List[Dict[str, str]] = []
    cards = page.query_selector_all('div[role="listitem"]')
    for card in cards:
        name_full_el = card.query_selector("span.name")
        if not name_full_el:
            continue
        full_name = name_full_el.inner_text().strip()

        parts = [p.strip() for p in full_name.split(",") if p.strip()]
        name = parts[0] if parts else ""
        country = parts[-1] if len(parts) > 1 else ""
        address = ", ".join(parts[1:-1]) if len(parts) > 2 else ""

        type_el = card.query_selector("span.type")
        btype = type_el.inner_text().strip() if type_el else ""

        coords_el = card.query_selector("p.coords")
        lat, lon = "", ""
        if coords_el:
            txt = coords_el.inner_text().strip()
            if "," in txt:
                lat, lon = [c.strip() for c in txt.split(",", 1)]

        results.append({
            "Name": name,
            "Address": address,
            "Country": country,
            "Type": btype,
            "Latitude": lat,
            "Longitude": lon
        })
    return results


def scrape_with_playwright(url: str, max_retries: int = 3, timeout_ms: int = 60000) -> List[Dict[str, str]]:
    """
    Load the UI page reliably with retries, generous timeouts, and reduced noise.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG_HEADED)
        context = browser.new_context(
            # A realistic User-Agent can help avoid edge blocks
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            bypass_csp=True,  # looser CSP sometimes helps dynamic UIs render
        )
        page = context.new_page()

        # Set default and navigation timeouts higher than the default 30s
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        _block_unnecessary_requests(page)

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # Use a lighter wait condition to avoid waiting for every asset.
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                # Ensure the results container exists first
                page.wait_for_selector('#searchresults', state="attached", timeout=timeout_ms)

                # Then wait specifically for at least one card
                page.wait_for_selector('div[role="listitem"]', state="visible", timeout=timeout_ms)

                # Small extra wait helps when content trickles in
                page.wait_for_timeout(500)

                data = parse_cards(page)
                if data:

                    if DEBUG_HEADED:
                        page.screenshot(path="output/page_ok.png", full_page=True)
                    browser.close()
                    return data

                # If no data found, try one more gentle wait, then retry
                page.wait_for_timeout(1500)
                data = parse_cards(page)
                if data:
                    if DEBUG_HEADED:
                        page.screenshot(path="output/page_ok2.png", full_page=True)
                    browser.close()
                    return data

                raise RuntimeError("No cards parsed (empty result).")

            except (PlaywrightTimeoutError, RuntimeError) as e:
                last_error = e
                # Optional: snapshot for debugging failed attempt
                try:
                    page.screenshot(path=f"output/page_error_attempt_{attempt}.png", full_page=True)
                except Exception:
                    pass
                # Hard refresh between attempts
                try:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    pass

        browser.close()
        # If all attempts failed, raise the last error with context
        raise RuntimeError(f"Failed after {max_retries} attempts: {last_error}")


def save_csv(data: List[Dict[str, str]], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Name", "Address", "Country", "Type", "Latitude", "Longitude"])
        writer.writeheader()
        writer.writerows(data)


def main():
    data = scrape_with_playwright(SEARCH_URL, max_retries=3, timeout_ms=70000)
    save_csv(data, OUT_CSV)
    print(f"Saved {len(data)} records to {OUT_CSV}")


if __name__ == "__main__":
    main()

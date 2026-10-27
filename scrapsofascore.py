import json
import time
from datetime import UTC, datetime
from pathlib import Path
from betboot.sofascore_import import new_capture, add_response, attach_ssr, historical_summary


def scrape_sofascore(event_id):
    from playwright.sync_api import sync_playwright
    scraped_data = new_capture(event_id)

    def handle_response(response):
        """Record only the requested event; retain each timestamped URL snapshot."""
        url = response.url
        if "/api/v1/" in url and response.status == 200:
            try:
                data = response.json()
                add_response(scraped_data, url, data,
                             collected_at=datetime.now(UTC).isoformat(), status=response.status)
            except (ValueError, TypeError):
                scraped_data['issues'].append({'type':'response_capture_failed'})

    with sync_playwright() as p:
        print("1. Launching Chromium...")
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        # Attach passive network listener
        page.on("response", handle_response)

        # Step 1: Load the homepage and let Sofascore's frontend initialize
        print("2. Loading Sofascore homepage...")
        page.goto("https://www.sofascore.com/", wait_until="domcontentloaded")
        time.sleep(6)

        # Dismiss cookie consent banner if it appears
        try:
            consent_btn = page.locator("button:has-text('Consent'), button:has-text('Accept'), button:has-text('Agree')").first
            if consent_btn.is_visible(timeout=2000):
                consent_btn.click()
                print("   Dismissed cookie banner.")
                time.sleep(1)
        except Exception:
            pass

        # Step 2: Test if the browser session can fetch the event metadata via same-origin XHR
        # using the exact headers Sofascore's frontend uses.
        print(f"\n3. Querying endpoints for Event ID: {event_id}...")
        endpoints = [
            f"event/{event_id}",
            f"event/{event_id}/statistics",
            f"event/{event_id}/lineups",
            f"event/{event_id}/incidents",
            f"event/{event_id}/shotmap",
            f"event/{event_id}/graph",
            f"event/{event_id}/h2h",
            f"event/{event_id}/best-players",
            f"event/{event_id}/pregame-form",
        ]

        for ep in endpoints:
            if any(r["endpoint"] == ep for r in scraped_data["responses"]):
                continue  # Already captured by listener

            api_url = f"https://www.sofascore.com/api/v1/{ep}"
            before = len(scraped_data["responses"])
            result = page.evaluate(
                """async (url) => {
                    try {
                        const res = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            headers: {
                                'Accept': '*/*',
                                'X-Requested-With': 'XMLHttpRequest'
                            }
                        });
                        return { status: res.status, data: res.ok ? await res.json() : null };
                    } catch (err) {
                        return { status: 0, error: err.toString() };
                    }
                }""",
                api_url,
            )

            status = result.get("status")
            if status == 200 and result.get("data"):
                if len(scraped_data["responses"]) == before:
                    add_response(scraped_data, api_url, result["data"],
                                 collected_at=datetime.now(UTC).isoformat(), status=status)
                print(f"  [Fetched] ✅ {ep}")
            else:
                scraped_data["issues"].append({"type":"request_failed","endpoint":ep,"http_status":status})
                print(f"  [Fetched] ⚠️ {ep} returned HTTP {status}")
                if status in (401, 403, 429):
                    print("Access denied or rate limited; stopping requests.")
                    browser.close()
                    return scraped_data
            time.sleep(1)

        # Step 3: If we got the event's slug & customId from event/{event_id}, navigate to the
        # real match page URL to trigger full UI rendering, SSR extraction, and remaining APIs.
        event_info = next((r["data"].get("event") for r in reversed(scraped_data["responses"])
                           if r["endpoint"] == f"event/{event_id}"), None)
        if event_info and event_info.get("slug") and event_info.get("customId"):
            slug = event_info["slug"]
            custom_id = event_info["customId"]
            real_match_url = f"https://www.sofascore.com/football/match/{slug}/{custom_id}#id:{event_id}"
            print(f"\n4. Navigating to full match page: {real_match_url}")
            page.goto(real_match_url, wait_until="domcontentloaded")
            time.sleep(6)

            # Scroll down the page to trigger lazy-loaded tabs (stats, lineups, shotmap)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            time.sleep(3)

        # Extract Next.js SSR data (__NEXT_DATA__) embedded in the HTML DOM
        try:
            next_data = page.evaluate(
                """() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? JSON.parse(el.innerText) : null;
                }"""
            )
            if next_data:
                if attach_ssr(scraped_data, next_data, collected_at=datetime.now(UTC).isoformat()):
                    print("  [DOM] Matched target event.")
                else:
                    print("  [DOM] Excluded SSR payload: event identity did not match.")
        except Exception as e:
            print(f"  [DOM] ⚠️ Could not extract __NEXT_DATA__: {e}")

        browser.close()

    return scraped_data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Capture one event using access you are permitted to use; no access-block bypass.")
    parser.add_argument('--event-id', required=True, help='Numeric target event ID; no hard-coded historical fixture')
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/sofascore'))
    args = parser.parse_args()
    new_capture(args.event_id)  # Validate before opening a browser.
    final_data = scrape_sofascore(args.event_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output_dir / f'sofascore-{args.event_id}-{stamp}.json'
    with output.open('x', encoding='utf-8') as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)
    output.chmod(0o600)
    print(f'Saved {len(final_data["responses"])} target-event snapshots: {output}')
    print('Validation:', json.dumps(historical_summary(final_data), ensure_ascii=False))

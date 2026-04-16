"""
app.py - PCM 2026 WorldTour CSV Generator
Scrapes ProCyclingStats and serves a downloadable CSV via Flask.
"""

import io
import csv
import time
import logging
from flask import Flask, Response, send_file, jsonify
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://www.procyclingstats.com"
TEAMS_URL = f"{BASE_URL}/teams.php?s=world-tour&year=2026"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
REQUEST_DELAY = 1.2   # seconds between requests
REQUEST_TIMEOUT = 15  # seconds per request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=".", static_url_path="")

# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def fetch(url: str) -> BeautifulSoup | None:
    """GET a page and return a BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.Timeout:
        log.error("Timeout fetching %s", url)
    except requests.exceptions.ConnectionError:
        log.error("Connection error fetching %s", url)
    except requests.exceptions.HTTPError as exc:
        log.error("HTTP %s for %s", exc.response.status_code, url)
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error fetching %s: %s", url, exc)
    return None


def get_team_urls() -> list[tuple[str, str]]:
    """
    Scrape the WorldTour 2026 team list page.
    Returns a list of (team_name, team_url) tuples.
    """
    log.info("Fetching team list from %s", TEAMS_URL)
    soup = fetch(TEAMS_URL)
    if soup is None:
        raise RuntimeError("Cannot reach ProCyclingStats team list page.")

    teams: list[tuple[str, str]] = []
    seen_slugs: set[str] = set()

    # PCS team list: <ul class="list"> with <li> items containing <a href="/team/...">
    for a in soup.select("ul.list li a"):
        href = a.get("href", "")
        if not href.startswith("/team/"):
            continue
        slug = href.strip("/").split("/")[-1]
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        team_name = a.get_text(strip=True)
        # Filter out year-less or invalid slugs
        if not team_name or not slug:
            continue
        teams.append((team_name, f"{BASE_URL}{href}"))

    log.info("Found %d teams", len(teams))
    return teams


def get_riders(team_url: str) -> list[str]:
    """
    Scrape the rider roster from a team page.
    Returns a list of rider names (cleaned, deduplicated).
    """
    soup = fetch(team_url)
    if soup is None:
        log.warning("Could not fetch team page: %s", team_url)
        return []

    riders: list[str] = []
    seen: set[str] = set()

    # PCS team page: riders are in <ul class="riders"> or a table with rider links
    # Primary selector: links pointing to /rider/...
    for a in soup.select("a[href^='/rider/']"):
        name = a.get_text(strip=True)
        if not name or name in seen:
            continue
        # Skip entries that look like links to rider stats (numbers/years)
        if name.isdigit() or len(name) < 3:
            continue
        seen.add(name)
        riders.append(name)

    if not riders:
        log.warning("No riders found for %s", team_url)

    return riders


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------

def build_csv(teams_data: list[tuple[str, str]]) -> str:
    """
    Build CSV content as a string.
    Format: Team,Rider
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Team", "Rider"])

    for team_name, riders in teams_data:
        for rider in riders:
            writer.writerow([team_name, rider])

    return output.getvalue()


def scrape_all() -> list[tuple[str, str]]:
    """
    Orchestrate the full scrape: teams → riders.
    Returns list of (team_name, riders_list) pairs with at least one rider.
    """
    team_urls = get_team_urls()
    if not team_urls:
        raise RuntimeError("No WorldTour teams found – the site structure may have changed.")

    results: list[tuple[str, list[str]]] = []

    for i, (team_name, team_url) in enumerate(team_urls, start=1):
        log.info("[%d/%d] Scraping %s ...", i, len(team_urls), team_name)
        riders = get_riders(team_url)
        if riders:
            results.append((team_name, riders))
        else:
            log.warning("Skipping '%s' – no riders found.", team_name)
        time.sleep(REQUEST_DELAY)

    if not results:
        raise RuntimeError("Scraping finished but no rider data was collected.")

    return results


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main HTML page from the project root."""
    return app.send_static_file("index.html")


@app.route("/generate")
def generate():
    """Scrape PCS, build CSV, return as a file download."""
    try:
        teams_data = scrape_all()
        csv_content = build_csv(teams_data)

        total_teams = len(teams_data)
        total_riders = sum(len(r) for _, r in teams_data)
        log.info("CSV ready: %d teams, %d riders.", total_teams, total_riders)

        buffer = io.BytesIO(csv_content.encode("utf-8-sig"))  # UTF-8 BOM for Excel
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype="text/csv",
            as_attachment=True,
            download_name="pcm_2026_teams.csv",
        )

    except RuntimeError as exc:
        log.error("Scraping failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected server error: %s", exc)
        return jsonify({"error": "An unexpected error occurred. Check the server log."}), 500


@app.route("/status")
def status():
    """Health-check endpoint."""
    return jsonify({"status": "ok", "message": "Server is running."})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 58)
    print("  PCM 2026 WorldTour CSV Generator")
    print("  Open http://127.0.0.1:5000 in your browser")
    print("=" * 58 + "\n")
    app.run(debug=False, port=5000)

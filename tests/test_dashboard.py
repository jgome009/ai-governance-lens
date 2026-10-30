"""
Phase 4: the dashboard must be a single, self-contained, offline HTML
file - no CDN, no external fonts, no network calls of any kind. This
test builds it fresh and scans the output for anything that would cause
the browser to make a network request.

The one allowed exception is the SVG XML namespace URI
("http://www.w3.org/2000/svg"), which document.createElementNS requires
as a literal constant string - browsers never fetch it, it's an XML
namespace identifier, not a resource reference.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import build_dashboard

ALLOWED_URL_SUBSTRINGS = [
    "http://www.w3.org/2000/svg",  # SVG namespace URI - a constant, never fetched
]

# Things that actually cause a network request if present in the HTML.
NETWORK_TRIGGER_PATTERNS = [
    re.compile(r"<script[^>]+\bsrc\s*=", re.IGNORECASE),
    re.compile(r"<link[^>]+\bhref\s*=", re.IGNORECASE),
    re.compile(r"<img[^>]+\bsrc\s*=\s*[\"']https?://", re.IGNORECASE),
    re.compile(r"\bfetch\s*\(", re.IGNORECASE),
    re.compile(r"\bXMLHttpRequest\b"),
    re.compile(r"\bWebSocket\s*\(", re.IGNORECASE),
    re.compile(r"@import\s+url", re.IGNORECASE),
    re.compile(r"url\(\s*[\"']?https?://", re.IGNORECASE),
]


def build_fresh_html(db_path) -> str:
    """db_path: the session-scoped fixture from conftest.py - depending on
    it (rather than connecting to build_dashboard.DB_PATH directly) is
    what guarantees the database has actually been generated first. A
    bare sqlite3.connect() on a path whose parent directory doesn't exist
    yet (true on a fresh clone, where data/synthetic/ isn't tracked)
    fails with 'unable to open database file' rather than creating it."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        kpi_data = build_dashboard.gather_data(conn)
    finally:
        conn.close()
    payload = {
        "kpi": kpi_data,
        "evaluation": build_dashboard.load_evaluation(),
        "docs": build_dashboard.KPI_DOCS,
        "general_docs": build_dashboard.GENERAL_DOCS,
    }
    return build_dashboard.render_html(payload)


def test_dashboard_has_no_network_trigger_tags(db_path):
    html = build_fresh_html(db_path)
    for pattern in NETWORK_TRIGGER_PATTERNS:
        match = pattern.search(html)
        assert match is None, f"dashboard.html contains a network-triggering pattern: {match.group(0)!r}"


def test_dashboard_has_no_unexplained_external_urls(db_path):
    html = build_fresh_html(db_path)
    for match in re.finditer(r"https?://\S+", html):
        url = match.group(0).rstrip("\"'<>),;")
        assert any(url.startswith(allowed) for allowed in ALLOWED_URL_SUBSTRINGS), (
            f"dashboard.html contains an unexplained external URL: {url!r}"
        )


def test_dashboard_has_no_cdn_or_webfont_references(db_path):
    html = build_fresh_html(db_path)
    lowered = html.lower()
    for forbidden in ["cdn.", "googleapis.com", "jsdelivr", "unpkg.com", "cloudflare.com",
                       "@font-face", "fonts.google"]:
        assert forbidden not in lowered, f"dashboard.html references {forbidden!r}"


def test_dashboard_builds_and_is_well_formed(db_path):
    html = build_fresh_html(db_path)
    assert html.strip().startswith("<!doctype html>")
    assert "</html>" in html
    assert "AI Governance Lens" in html
    # Embedded data block must be valid JSON (sanity: build didn't truncate/corrupt it).
    import json
    data_match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert data_match is not None
    payload = json.loads(data_match.group(1))
    assert "kpi" in payload and "evaluation" in payload and "docs" in payload

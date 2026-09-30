"""The page loads nothing from the internet: Chart.js and the fonts are served from static/."""
import re

from fastapi.testclient import TestClient

import app

STATIC = app.BASE_DIR / "static"


def client():
    return TestClient(app.app, base_url="http://127.0.0.1:8080")


def test_page_loads_nothing_from_the_internet():
    html = client().get("/").text
    refs = re.findall(r'<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"', html)
    assert any("chart.umd.js" in r for r in refs) and any("fonts.css" in r for r in refs)
    assert [r for r in refs if r.startswith(("http:", "https:", "//"))] == []


def test_stylesheets_load_nothing_from_the_internet():
    for css in STATIC.rglob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "@import" not in text, css
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", text), css


def test_every_font_the_stylesheet_names_is_served():
    css = (STATIC / "fonts" / "fonts.css").read_text(encoding="utf-8")
    assert set(re.findall(r"font-family:\s*'([^']+)'", css)) == {"Sora", "DM Sans", "DM Mono"}
    files = re.findall(r"url\('([^']+)'\)", css)
    assert len(files) == 8
    c = client()
    for name in files:
        r = c.get(f"/static/fonts/{name}")
        assert r.status_code == 200, name
        assert r.content[:4] == b"wOF2", name


def test_chart_js_is_served_locally():
    r = client().get("/static/vendor/chart.umd.js")
    assert r.status_code == 200
    assert b"Chart.js v4.4.4" in r.content[:300]


def test_vendored_files_carry_their_licenses():
    for name in ("vendor/LICENSE-chart.js.md", "fonts/LICENSE-Sora.txt",
                 "fonts/LICENSE-DM-Sans.txt", "fonts/LICENSE-DM-Mono.txt"):
        assert (STATIC / name).stat().st_size > 500, name

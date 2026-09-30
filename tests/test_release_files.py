"""Files a release build depends on, and where the version shows."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

import app
import version

REPO = Path(__file__).parent.parent


def test_version_is_a_bare_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__)


def test_runtime_requirements_are_pinned():
    lines = (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
    reqs = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    assert {r.split("==")[0].lower() for r in reqs} == {"fastapi", "uvicorn", "jinja2", "orjson"}
    for req in reqs:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==\d[\w.]*", req), req


def test_the_conda_env_installs_the_runtime_requirements():
    assert "- -r requirements.txt" in (REPO / "environment.yml").read_text(encoding="utf-8")


def test_the_settings_panel_and_the_api_show_the_version():
    client = TestClient(app.app, base_url="http://127.0.0.1:8080")
    assert f"Claude Usage Dashboard {version.__version__}" in client.get("/").text
    assert app.app.version == version.__version__


def test_the_repo_carries_an_mit_license_and_a_readme():
    license_text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 Jonathan Weaver" in license_text
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Claude Usage Dashboard")

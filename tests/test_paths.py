import sys
from pathlib import Path

import paths

REPO = Path(paths.__file__).parent


def test_source_run_keeps_files_in_the_repo(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CUD_DATA_DIR", raising=False)
    assert paths.resource_dir() == REPO
    assert paths.data_dir() == REPO / "data"
    assert paths.log_dir() == REPO / "logs"


def test_frozen_build_keeps_data_in_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.delenv("CUD_DATA_DIR", raising=False)
    assert paths.resource_dir() == tmp_path / "bundle"
    assert paths.data_dir() == tmp_path / "lad" / "ClaudeUsageDashboard" / "data"
    assert paths.log_dir() == tmp_path / "lad" / "ClaudeUsageDashboard" / "logs"


def test_env_var_overrides_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("CUD_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"


def test_app_takes_its_paths_from_paths():
    import app
    assert app.BASE_DIR == paths.RESOURCE_DIR
    assert app.QUOTA_CACHE_FILE == paths.DATA_DIR / "quota_cache.json"

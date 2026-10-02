"""Release build inputs: the icon and version resource drawn from the app, and the PyInstaller spec."""
import importlib.util
from pathlib import Path

from PIL import Image

import tray
import version

REPO = Path(__file__).parent.parent


def load(name):
    """Import a module from packaging/. It isn't a package: `packaging` is also a PyPI library's name."""
    spec = importlib.util.spec_from_file_location(name, REPO / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_assets = load("build_assets")


def test_file_version_pads_the_release_number_to_four_parts():
    assert build_assets.file_version("2.0.0") == (2, 0, 0, 0)
    assert build_assets.file_version("2.10") == (2, 10, 0, 0)


def test_the_icon_holds_the_tray_ring_drawn_at_every_size(tmp_path):
    path = build_assets.write_icon(tmp_path / "icon.ico")
    with Image.open(path) as ico:
        assert sorted(ico.info["sizes"]) == [(s, s) for s in build_assets.ICON_SIZES]
        for size in (16, 32, 256):  # each drawn at its own size, not scaled from 256
            stored = ico.ico.getimage((size, size)).convert("RGBA")
            assert stored.tobytes() == tray.icon_image(False, size).tobytes()


def test_the_version_resource_names_the_app_for_windows():
    calls = {}

    def recorder(kind):
        def build(*args, **kwargs):
            calls.setdefault(kind, []).append((args, kwargs))
            return kind
        return build

    kinds = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct",
             "VarFileInfo", "VarStruct")
    eval(build_assets.version_info("2.0.0"), {kind: recorder(kind) for kind in kinds})  # as PyInstaller reads it
    strings = dict(args for args, _ in calls["StringStruct"])
    assert strings["FileDescription"] == strings["ProductName"] == "Claude Usage Dashboard"
    assert strings["FileVersion"] == strings["ProductVersion"] == "2.0.0"
    assert strings["OriginalFilename"] == "ClaudeUsageDashboard.exe"
    ((_, fixed),) = calls["FixedFileInfo"]
    assert fixed["filevers"] == fixed["prodvers"] == (2, 0, 0, 0)


def test_write_puts_both_files_in_the_build_folder(tmp_path):
    icon, info = build_assets.write(tmp_path / "assets")
    assert Path(icon) == tmp_path / "assets" / "icon.ico" and Path(icon).is_file()
    assert Path(info).read_text(encoding="utf-8") == build_assets.version_info(version.__version__)


def test_the_spec_builds_a_windowed_exe_with_the_page_files():
    spec = (REPO / "packaging" / "ClaudeUsageDashboard.spec").read_text(encoding="utf-8")
    assert "desktop.py" in spec and "console=False" in spec
    assert '"static"' in spec and '"templates"' in spec
    for module in ("uvicorn.lifespan.on", "uvicorn.protocols.http.h11_impl",
                   "uvicorn.protocols.websockets.auto", "uvicorn.loops.asyncio", "pystray._win32"):
        assert f'"{module}"' in spec, module
    assert 'excludes=["setuptools"]' in spec  # else its runtime hook imports it at every start
    assert spec.count(f'name="{build_assets.EXE_NAME.removesuffix(".exe")}"') == 2  # EXE and COLLECT


def test_build_tools_are_pinned_apart_from_the_runtime():
    text = (REPO / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    reqs = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert reqs == ["pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.8"]

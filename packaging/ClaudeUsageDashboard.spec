# PyInstaller spec for ClaudeUsageDashboard.exe: one folder, no console window.
#
# Build from the repo root, in an env with requirements.txt and packaging/requirements-build.txt:
#     python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec
# Output: dist/ClaudeUsageDashboard/ (the exe and its _internal folder), which
# packaging/installer.iss packs into the installer.
import os
import sys

ROOT = os.path.dirname(SPECPATH)
sys.path[:0] = [SPECPATH, ROOT]  # build_assets, and the app modules it draws from
import build_assets  # noqa: E402

icon_file, version_file = build_assets.write(os.path.join(ROOT, "build", "assets"))

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    datas=[
        # paths.RESOURCE_DIR is sys._MEIPASS (the _internal folder) in a frozen build
        (os.path.join(ROOT, "static"), "static"),
        (os.path.join(ROOT, "templates"), "templates"),
    ],
    hiddenimports=[
        # uvicorn/config.py imports these by name at run time, out of the analysis' sight
        "uvicorn.lifespan.on",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.logging",
        # pystray chooses its backend with importlib
        "pystray._win32",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClaudeUsageDashboard",
    console=False,  # windowed: desktop.setup_logging sends output to the log file
    icon=icon_file,
    version=version_file,
    upx=False,  # UPX-packed exes trip more antivirus heuristics
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClaudeUsageDashboard", upx=False)

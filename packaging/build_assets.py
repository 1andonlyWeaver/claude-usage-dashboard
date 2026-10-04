"""
Build inputs drawn from the app itself, so the exe can't drift from it:
- icon.ico: tray.py's ring, drawn at each size Windows asks for. The exe, its window, the
  taskbar and the installer all use it.
- version_info.txt: the exe's Windows version resource. Its FileDescription is the name
  Windows puts on the app's notifications, its taskbar button and Task Manager (from source
  they say "Python", python.exe's own description).
ClaudeUsageDashboard.spec writes both into build/assets before PyInstaller runs.
"""
from pathlib import Path

import tray
import version

APP_NAME = "Claude Usage Dashboard"
EXE_NAME = "ClaudeUsageDashboard.exe"
AUTHOR = "Jonathan Weaver"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def file_version(number: str) -> tuple[int, int, int, int]:
    """'2.0.0' -> (2, 0, 0, 0): the four numbers a version resource holds."""
    parts = [int(part) for part in number.split(".")]
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"not a release number: {number!r}")
    return tuple(parts + [0] * (4 - len(parts)))


def write_icon(path: Path) -> Path:
    """Each size drawn on its own rather than scaled down from 256, so the ring stays crisp at 16 px."""
    images = [tray.icon_image(False, size) for size in ICON_SIZES]
    images[-1].save(path, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1])
    return path


def version_info(number: str) -> str:
    """The version resource, in the text form PyInstaller's EXE(version=...) reads (it evals it)."""
    numbers = file_version(number)
    strings = {
        "CompanyName": AUTHOR,
        "FileDescription": APP_NAME,
        "FileVersion": number,
        "InternalName": EXE_NAME.removesuffix(".exe"),
        "LegalCopyright": f"Copyright (c) 2026 {AUTHOR}. MIT License.",
        "OriginalFilename": EXE_NAME,
        "ProductName": APP_NAME,
        "ProductVersion": number,
    }
    table = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{table}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def write(out_dir) -> tuple[str, str]:
    """Write icon.ico and version_info.txt into out_dir. Returns their paths, for the spec."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    icon = write_icon(out / "icon.ico")
    info = out / "version_info.txt"
    info.write_text(version_info(version.__version__), encoding="utf-8")
    return str(icon), str(info)

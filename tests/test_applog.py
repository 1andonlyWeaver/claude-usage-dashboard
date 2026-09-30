"""The dashboard's log file: UTF-8, rotated to .1 at startup once it passes 5 MB."""
import io
import sys

import applog


def test_a_small_log_stays_put(tmp_path):
    log = tmp_path / "dashboard.log"
    log.write_bytes(b"x" * 100)
    applog.rotate(log, max_bytes=100)
    assert log.read_bytes() == b"x" * 100
    assert not (tmp_path / "dashboard.log.1").exists()


def test_a_big_log_moves_to_dot_one_replacing_the_old_one(tmp_path):
    log = tmp_path / "dashboard.log"
    older = tmp_path / "dashboard.log.1"
    older.write_text("the run before last")
    log.write_bytes(b"x" * 101)
    applog.rotate(log, max_bytes=100)
    assert not log.exists()
    assert older.read_bytes() == b"x" * 101


def test_a_missing_log_is_fine(tmp_path):
    applog.rotate(tmp_path / "dashboard.log", max_bytes=1)
    assert list(tmp_path.iterdir()) == []


def test_rotation_failure_is_not_fatal(tmp_path, monkeypatch):
    log = tmp_path / "dashboard.log"
    log.write_bytes(b"x" * 101)

    def held_open(src, dst):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(applog.os, "replace", held_open)
    applog.rotate(log, max_bytes=100)
    assert log.read_bytes() == b"x" * 101


def test_open_log_rotates_first_and_writes_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(applog, "MAX_BYTES", 10)
    log = tmp_path / "logs" / "dashboard.log"
    log.parent.mkdir()
    log.write_bytes(b"x" * 11)
    f = applog.open_log(log)
    try:
        print("résumé ✓ ☃ 🚀", file=f)
    finally:
        f.close()
    assert (tmp_path / "logs" / "dashboard.log.1").read_bytes() == b"x" * 11
    assert log.read_text(encoding="utf-8").strip() == "résumé ✓ ☃ 🚀"


def test_open_log_creates_the_folder(tmp_path):
    f = applog.open_log(tmp_path / "new" / "dashboard.log")
    f.close()
    assert (tmp_path / "new" / "dashboard.log").exists()


def test_utf8_stdio_makes_print_safe_on_a_cp1252_stream(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", None)  # pythonw.exe has none
    applog.utf8_stdio()
    assert stream.line_buffering
    print("☃ 🚀")
    stream.flush()
    assert raw.getvalue().decode("utf-8").strip() == "☃ 🚀"

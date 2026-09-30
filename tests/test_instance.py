"""One copy per session, runtime.json, and how a second launch reaches the first."""
import http.server
import json
import os
import socket
import threading
import uuid

import pytest

import instance


def unique_name():
    return rf"Local\ClaudeUsageDashboardTest-{uuid.uuid4().hex}"


def test_the_first_copy_gets_the_mutex_and_a_second_does_not():
    name = unique_name()
    first = instance.acquire(name)
    assert first
    try:
        assert instance.acquire(name) is None
    finally:
        instance.release(first)
    again = instance.acquire(name)  # Windows drops the mutex with its last handle
    assert again
    instance.release(again)


def test_runtime_round_trip(isolated_runtime):
    instance.write_runtime(8765)
    assert instance.read_runtime() == {"port": 8765, "pid": os.getpid()}
    assert json.loads(isolated_runtime.read_text(encoding="utf-8")) == {"port": 8765, "pid": os.getpid()}


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"port": "8765", "pid": 1}',
                                     '{"port": 0, "pid": 1}', '{"port": 70000, "pid": 1}',
                                     '{"port": 8765}', '{"port": true, "pid": 1}'])
def test_a_missing_or_garbled_runtime_file_reads_as_none(isolated_runtime, content):
    assert instance.read_runtime() is None
    isolated_runtime.write_text(content, encoding="utf-8")
    assert instance.read_runtime() is None


def test_clear_runtime_only_removes_its_own_file(isolated_runtime):
    instance.write_runtime(8765)
    instance.clear_runtime(pid=os.getpid() + 1)
    assert isolated_runtime.exists()
    instance.clear_runtime(pid=os.getpid())
    assert not isolated_runtime.exists()
    instance.write_runtime(8765)
    instance.clear_runtime()  # no pid: whatever is there goes
    assert not isolated_runtime.exists()
    instance.clear_runtime()  # nothing there: fine


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        found = self.path == "/api/app/show"
        body = b'{"shown": true}' if found else b'{"detail": "Not Found"}'
        self.send_response(200 if found else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()
    server.server_close()


def test_call_ignores_a_proxy_in_the_environment(monkeypatch, local_server):
    # urllib's default opener would send this through the dead proxy and fail.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    assert instance.call(local_server, "/api/app/show", method="POST") == b'{"shown": true}'


def test_call_raises_oserror_for_an_error_status(local_server):
    with pytest.raises(OSError):
        instance.call(local_server, "/api/nope", method="POST")


def test_call_raises_oserror_when_something_else_answers():
    """A stale runtime.json can name a port that some other program now answers on."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def answer_garbage():
        conn, _ = listener.accept()
        conn.recv(1024)
        conn.sendall(b"hello\r\n\r\n")
        conn.close()

    threading.Thread(target=answer_garbage, daemon=True).start()
    try:
        with pytest.raises(OSError):
            instance.call(listener.getsockname()[1], "/api/app/show", method="POST")
    finally:
        listener.close()


class Clock:
    """Fake time for show_running: sleeping advances it instantly."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_show_running_asks_the_port_in_runtime_json(monkeypatch):
    instance.write_runtime(8765)
    calls = []

    def fake_call(port, path, method="GET", timeout=5.0):
        calls.append((port, path, method))
        return b'{"shown": true}'

    monkeypatch.setattr(instance, "call", fake_call)
    clock = Clock()
    assert instance.show_running(sleep=clock.sleep, clock=clock.time) is True
    assert calls == [(8765, "/api/app/show", "POST")]
    assert clock.sleeps == []


def test_show_running_waits_for_a_copy_that_is_still_starting(monkeypatch):
    """No runtime.json yet, then the previous run's port (refused), then the real one."""
    clock = Clock()

    def fake_sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            instance.write_runtime(9999)
        if len(clock.sleeps) == 4:
            instance.write_runtime(8765)

    def fake_call(port, path, method="GET", timeout=5.0):
        if port != 8765:
            raise ConnectionRefusedError(10061, "No connection could be made")
        return b'{"shown": true}'

    monkeypatch.setattr(instance, "call", fake_call)
    assert instance.show_running(sleep=fake_sleep, clock=clock.time) is True
    assert len(clock.sleeps) == 4


def test_show_running_gives_up_after_the_timeout(monkeypatch):
    instance.write_runtime(8765)

    def refused(*args, **kwargs):
        raise ConnectionRefusedError(10061, "No connection could be made")

    monkeypatch.setattr(instance, "call", refused)
    clock = Clock()
    assert instance.show_running(timeout=10, sleep=clock.sleep, clock=clock.time) is False
    assert clock.now == 10.0 and len(clock.sleeps) == 20

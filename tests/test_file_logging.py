"""Optional file logging must preserve console, request timing and admin logs."""
import io
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import observability
from src.config import Settings
from src.logging_ring import RingBufferLogHandler


@pytest.fixture
def isolated_logging(monkeypatch):
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    old_file = observability._file_handler
    for handler in old_handlers:
        root.removeHandler(handler)
    root.setLevel(logging.INFO)
    console = io.StringIO()
    root.addHandler(logging.StreamHandler(console))
    monkeypatch.setattr(observability, "_file_handler", None)
    try:
        yield SimpleNamespace(root=root, console=console)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in old_handlers:
            root.addHandler(handler)
        root.setLevel(old_level)
        observability._file_handler = old_file


def configure(monkeypatch, path, **kwargs):
    settings = Settings(_env_file=None, log_file_path=str(path), **kwargs)
    monkeypatch.setattr(observability, "get_settings", lambda: settings)
    observability.configure_logging()
    return settings


def test_default_is_console_only(monkeypatch, isolated_logging, tmp_path):
    path = tmp_path / "not-created" / "server.log"
    settings = configure(monkeypatch, path)
    assert settings.log_file_enabled is False
    logging.getLogger("beecount.test").info("console-only marker")
    assert "console-only marker" in isolated_logging.console.getvalue()
    assert not path.parent.exists()


def test_file_console_and_admin_buffer_coexist(monkeypatch, isolated_logging, tmp_path):
    path = tmp_path / "logs" / "server.log"
    ring = RingBufferLogHandler()
    configure(monkeypatch, path, log_file_enabled=True)
    isolated_logging.root.addHandler(ring)
    observability.configure_logging()  # main setup and middleware both configure logging
    logger = logging.getLogger("beecount.test")
    logger.info("中文日志 marker")
    try:
        raise RuntimeError("test exception marker")
    except RuntimeError:
        logger.exception("exception captured")
    content = path.read_text(encoding="utf-8")
    assert content.count("中文日志 marker") == 1
    assert "INFO beecount.test" in content
    assert "RuntimeError: test exception marker" in content
    assert "中文日志 marker" in isolated_logging.console.getvalue()
    assert any("中文日志 marker" in entry["message"] for entry in ring.snapshot())
    assert len([h for h in isolated_logging.root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]) == 1


def test_rotation_is_bounded(monkeypatch, isolated_logging, tmp_path):
    path = tmp_path / "server.log"
    configure(monkeypatch, path, log_file_enabled=True,
              log_file_max_bytes=200, log_file_backup_count=2)
    for index in range(20):
        logging.getLogger("beecount.test").info("row-%02d %s", index, "x" * 60)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "server.log", "server.log.1", "server.log.2"]
    assert "row-19" in path.read_text()
    assert "row-00" not in "".join(p.read_text() for p in tmp_path.iterdir())


def test_reconfigure_and_disable_close_file(monkeypatch, isolated_logging, tmp_path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    configure(monkeypatch, first, log_file_enabled=True)
    old = observability._file_handler
    configure(monkeypatch, second, log_file_enabled=True)
    assert old.stream is None
    logging.getLogger("beecount.test").info("second only")
    assert "second only" not in first.read_text()
    assert "second only" in second.read_text()
    current = observability._file_handler
    configure(monkeypatch, second, log_file_enabled=False)
    assert current.stream is None
    assert observability._file_handler is None


def test_http_rt_and_exception_status_are_recorded(monkeypatch, isolated_logging, tmp_path):
    path = tmp_path / "http.log"
    configure(monkeypatch, path, log_file_enabled=True)
    app = FastAPI()
    observability.install_request_middleware(app)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/fail")
    def fail():
        raise RuntimeError("request failed")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/ok", headers={"X-Request-ID": "rt-test"})
        assert response.status_code == 200
        assert float(response.headers["X-Response-Time-Ms"]) >= 0
        assert response.headers["X-Request-ID"] == "rt-test"
        assert client.get("/fail").status_code == 500
    content = path.read_text()
    assert "GET /ok → 200" in content
    assert "req=rt-test" in content
    assert "GET /fail → 500" in content


def test_invalid_file_settings_fail_explicitly(monkeypatch, isolated_logging, tmp_path):
    with pytest.raises(ValueError, match="LOG_FILE_PATH"):
        configure(monkeypatch, " ", log_file_enabled=True)
    for key in ["log_file_max_bytes", "log_file_backup_count"]:
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{key: 0})

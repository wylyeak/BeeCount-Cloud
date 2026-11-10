from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.responses import Response

from .config import get_settings
from .metrics import metrics

_file_handler: RotatingFileHandler | None = None
_logging_lock = threading.RLock()


def configure_logging() -> None:
    """Keep console logging and optionally add one rotating application file."""
    global _file_handler
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    root = logging.getLogger()
    with _logging_lock:
        if not settings.log_file_enabled:
            if _file_handler is not None:
                root.removeHandler(_file_handler)
                _file_handler.close()
                _file_handler = None
            return

        if not settings.log_file_path.strip():
            raise ValueError("LOG_FILE_PATH must not be empty when LOG_FILE_ENABLED=true")
        path = Path(settings.log_file_path).expanduser().absolute()
        if (
            _file_handler is not None
            and _file_handler in root.handlers
            and _file_handler.baseFilename == str(path)
            and _file_handler.maxBytes == settings.log_file_max_bytes
            and _file_handler.backupCount == settings.log_file_backup_count
        ):
            return

        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        if _file_handler is not None:
            root.removeHandler(_file_handler)
            _file_handler.close()
        root.addHandler(handler)
        _file_handler = handler


def install_request_middleware(app: FastAPI) -> None:
    logger = logging.getLogger("beecount.access")
    configure_logging()

    @app.middleware("http")
    async def request_observer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        metrics.inc("beecount_http_requests_total")

        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            metrics.inc("beecount_http_errors_total")
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metrics.inc(f"beecount_http_status_{status_code // 100}xx_total")
            # 人类可读格式,ring buffer 里直接看也舒服。保留 requestId 方便
            # 跨日志对照(应用日志会用同一个 requestId 作为 extra 输出)。
            logger.info(
                "%s %s → %d %.1fms req=%s",
                request.method,
                request.url.path,
                status_code,
                elapsed_ms,
                request_id,
            )

        if response is None:
            raise RuntimeError("response missing")
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
        return response

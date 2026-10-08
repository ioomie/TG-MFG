"""Bounded, structured diagnostics. Never pass request bodies or exception text here."""
import json
import logging
import platform
from importlib.metadata import version
import threading
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

CORE_VERSION = "1.6.0"
ROUTES = {"/api/bootstrap", "/api/status", "/api/connect", "/api/send-code", "/api/sign-in",
          "/api/channels", "/api/scan", "/api/results", "/api/cancel", "/api/channel-count",
          "/api/disconnect", "/api/shutdown", "/api/diagnostics", "/api/debug", "/api/test-proxy",
          "/api/message-block", "/api/scan-speed", "/api/storage", "/api/cache-open",
          "/api/restore-login", "/api/refresh-messages", "/", "/index.html"}
FIELDS = {"method", "route", "status", "operation", "result", "duration_ms", "error_type",
          "errno", "winerror", "proxy_enabled", "proxy_type", "proxy_backend", "proxy_host", "proxy_port",
          "origin_match", "fetch_site", "port", "event_loop"}


def error_fields(exc):
    return {"error_type": type(exc).__name__, **{key: value for key in ("errno", "winerror")
            if isinstance(value := getattr(exc, key, None), int)}}


class Diagnostics:
    def __init__(self, path=None, enabled=False):
        self.path = path
        self.enabled = False
        self.events = deque(maxlen=200)
        self.lock = threading.RLock()
        self.handler = None
        self.set_enabled(enabled)

    def set_enabled(self, enabled):
        with self.lock:
            if self.handler:
                self.handler.close()
                self.handler = None
            if enabled and self.path:
                self.handler = RotatingFileHandler(self.path, maxBytes=262144, backupCount=1, encoding="utf-8")
                self.handler.setFormatter(logging.Formatter("%(message)s"))
            self.enabled = bool(enabled)

    def record(self, event, **fields):
        item = {"time": datetime.now(timezone.utc).isoformat(), "event": event,
                **{key: value for key, value in fields.items() if key in FIELDS
                   and isinstance(value, (str, int, float, bool))}}
        with self.lock:
            self.events.append(item)
            if self.handler:
                # Only our explicit metadata whitelist is written; no arbitrary exception messages.
                try:
                    self.handler.emit(logging.LogRecord("tg-mfg", logging.INFO, "", 0,
                                      json.dumps(item, ensure_ascii=False), (), None))
                except OSError:
                    pass

    def snapshot(self):
        with self.lock:
            return {"core_version": CORE_VERSION, "debug_enabled": self.enabled,
                    "environment": {"system": platform.system(), "python": platform.python_version(),
                                    "architecture": platform.machine(),"proxy_backend":"python-socks",
                                    "proxy_version":version('python-socks')}, "events": list(self.events)}

    def close(self):
        self.set_enabled(False)

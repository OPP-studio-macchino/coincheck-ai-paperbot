"""Bounded, fixed-endpoint Responses transport for the isolated generator only."""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import threading

from .contracts import LabError, canonical_json, strict_json
from .phase2_policy import MODEL, MAX_OUTPUT_TOKENS
from .provider import build_responses_request

HOST = "api.openai.com"
PATH = "/v1/responses"
MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 65536
TOTAL_TIMEOUT_SECONDS = 30
CONTAINER_KEY_PATH = Path("/run/secrets/openai_api_key")


def read_api_key(environ=None) -> str:
    """Read one bounded descriptor, never echo its value or a filesystem error.

    Host files must be owner-private. The sole 0444 exception is a read-only
    container secret mount at the fixed path; this is not a host secret vault.
    """
    env = os.environ if environ is None else environ
    location = env.get("OPENAI_API_KEY_FILE")
    if not isinstance(location, str) or not location or len(location) > 4096:
        raise LabError("PHASE2_KEY_FILE_REQUIRED")
    fd = None
    try:
        path = Path(location)
        if not path.is_absolute() or ".." in path.parts:
            raise LabError("PHASE2_KEY_FILE_INVALID")
        for part in [path, *path.parents]:
            if part.is_symlink():
                raise LabError("PHASE2_KEY_FILE_INVALID")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 16 <= info.st_size <= 513
                or info.st_uid not in (0, os.geteuid())):
            raise LabError("PHASE2_KEY_FILE_INVALID")
        mounted = (path == CONTAINER_KEY_PATH and mode == 0o444
                   and bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY))
        if not mounted:
            parent = path.parent.stat()
            if (mode not in (0o400, 0o600) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(parent.st_mode) & 0o077
                    or parent.st_uid != os.geteuid()):
                raise LabError("PHASE2_KEY_FILE_PERMISSIONS")
        raw = os.read(fd, 514)
        if len(raw) != info.st_size or os.fstat(fd).st_size != info.st_size:
            raise LabError("PHASE2_KEY_FILE_INVALID")
        value = raw.decode("ascii").rstrip("\n")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", value):
            raise LabError("PHASE2_KEY_FILE_INVALID")
        return value
    except LabError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise LabError("PHASE2_KEY_FILE_INVALID") from None
    finally:
        if fd is not None:
            os.close(fd)


def validate_request(request: dict) -> bytes:
    """The caller cannot inject tools, a previous response, a URL or a tier."""
    try:
        context = strict_json(request["input"][1]["content"])
        expected = build_responses_request(context, model=MODEL,
                                           max_output_tokens=MAX_OUTPUT_TOKENS)
        expected["service_tier"] = "default"
        expected["truncation"] = "disabled"
        if request != expected:
            raise LabError("PHASE2_REQUEST_INVALID")
        body = canonical_json(request).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise LabError("PHASE2_REQUEST_INVALID")
        return body
    except (KeyError, TypeError, IndexError, ValueError, OverflowError, RecursionError):
        raise LabError("PHASE2_REQUEST_INVALID") from None


class ResponsesGateway:
    """One TLS POST, no redirects/retries/proxies, bounded wall-clock duration.

    The Linux/macOS one-shot CLI runs on the main thread. Refuse unsupported
    threading/timer environments rather than advertise a false total timeout.
    """
    def __init__(self, api_key: str, *, connection_factory=http.client.HTTPSConnection):
        if not isinstance(api_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", api_key):
            raise LabError("PHASE2_KEY_FILE_INVALID")
        self._api_key = api_key
        self._connection_factory = connection_factory

    def send(self, request: dict) -> dict:
        body = validate_request(request)
        if (threading.current_thread() is not threading.main_thread()
                or not hasattr(signal, "setitimer")
                or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
            raise LabError("PHASE2_TIMEOUT_ENVIRONMENT")
        connection = None
        previous_handler = signal.getsignal(signal.SIGALRM)

        def expired(_signum, _frame):
            raise TimeoutError("PHASE2_DEADLINE")

        try:
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, TOTAL_TIMEOUT_SECONDS)
            connection = self._connection_factory(
                HOST, timeout=TOTAL_TIMEOUT_SECONDS, context=ssl.create_default_context())
            connection.request("POST", PATH, body=body, headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json", "Accept": "application/json"})
            reply = connection.getresponse()
            if reply.status != 200:
                raise LabError("PHASE2_HTTP_FAILED")
            raw = reply.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise LabError("PHASE2_HTTP_INVALID")
            value = strict_json(raw, max_bytes=MAX_RESPONSE_BYTES)
            if not isinstance(value, dict):
                raise LabError("PHASE2_HTTP_INVALID")
            return value
        except Exception:
            raise LabError("PHASE2_HTTP_FAILED") from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

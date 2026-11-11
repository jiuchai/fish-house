"""Web API 共用工具：统一响应与同步 HTTP 请求。"""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from typing import Any

from astrbot.api.web import json_response



class ApiBaseMixin:
    @staticmethod
    def _ok(**data: Any):
        return json_response({"status": "ok", **data})

    @staticmethod
    def _http_sync(
        url: str,
        method: str = "GET",
        token: str = "",
        body: Any = None,
        multipart: tuple[str, bytes] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        headers = {"User-Agent": "astrbot-fish-house"}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if multipart is not None:
            boundary, payload = multipart
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            data = payload
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Type", "").startswith("application/json"):
                    return json.loads(raw.decode("utf-8"))
                return raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
                message = payload.get("message") or payload.get("detail") or str(exc)
            except Exception:
                message = raw.decode("utf-8", "ignore")[:200] or str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {message}") from exc

    @staticmethod
    def _multipart_file(filename: str, data: bytes) -> tuple[str, bytes]:
        boundary = "----fishhouse" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        return boundary, head + data + tail

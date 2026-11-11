"""主题市场客户端（登录、列表、下载、上传）。"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid
import urllib.request
from typing import Any

from astrbot.api.web import error_response, request

from .. import theme_store



class MarketApiMixin:
    def _download_bytes(self, url: str, token: str = "", total_hint: Any = 0) -> bytes:
        """流式下载主题包，并把进度写到 market_progress 供面板轮询。"""
        headers = {"User-Agent": "astrbot-fish-house"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        chunks: list[bytes] = []
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                try:
                    total = int(resp.headers.get("Content-Length") or total_hint or 0)
                except (TypeError, ValueError):
                    total = 0
                self.market_progress.update(
                    active=True, phase="下载主题包", received=0, total=total, error=""
                )
                received = 0
                while True:
                    # 面板点了「取消下载」时，读完当前分块就中断。
                    if getattr(self, "market_cancel", False):
                        raise RuntimeError("下载已取消")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    self.market_progress.update(received=received)
                if total <= 0:
                    self.market_progress["total"] = received
                return b"".join(chunks)
        except Exception as exc:
            self.market_progress.update(active=False, phase="下载失败", error=str(exc))
            raise

    async def api_theme_market(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        if action == "progress":
            return self._ok(progress=dict(self.market_progress))
        if action == "cancel_download":
            self.market_cancel = True
            return self._ok(cancelled=True)
        base = str(payload.get("url") or self.theme_market_url).rstrip("/")
        if not base.startswith(("http://", "https://")):
            return error_response("市场地址必须是 http(s)")
        token = str(payload.get("token") or "")
        try:
            if action in {"session_get", "session_save", "session_clear"}:
                username = getattr(request, "username", None)
                if not username:
                    return self._ok(token="", username="", url="")
                sessions = self._market_sessions()
                key = str(username)
                if action == "session_get":
                    record = sessions.get(key) or {}
                    record_url = str(record.get("url") or "").rstrip("/")
                    current_url = str(self.theme_market_url or "").rstrip("/")
                    if record_url and current_url and record_url != current_url:
                        return self._ok(token="", username="", url=current_url)
                    return self._ok(
                        token=str(record.get("token") or ""),
                        username=str(record.get("username") or ""),
                        url=str(record.get("url") or ""),
                    )
                if action == "session_clear":
                    sessions.pop(key, None)
                    self._write_market_sessions(sessions)
                    return self._ok(cleared=True)
                token_value = str(payload.get("token") or "")
                if not token_value:
                    return error_response("会话 token 为空")
                sessions[key] = {
                    "token": token_value,
                    "username": str(payload.get("username") or ""),
                    "url": str(payload.get("url") or ""),
                    "saved_at": time.time(),
                }
                self._write_market_sessions(sessions)
                return self._ok(saved=True)
            if action in {"login", "register"}:
                result = await asyncio.to_thread(
                    self._http_sync,
                    f"{base}/api/{action}",
                    "POST",
                    "",
                    {
                        "username": str(payload.get("username") or ""),
                        "password": str(payload.get("password") or ""),
                        "email": str(payload.get("email") or ""),
                    },
                )
                return self._ok(**result, url=base)
            if action == "list":
                query = str(payload.get("q") or "").strip()
                try:
                    limit = max(1, min(50, int(payload.get("limit") or 12)))
                except (TypeError, ValueError):
                    limit = 12
                try:
                    offset = max(0, int(payload.get("offset") or 0))
                except (TypeError, ValueError):
                    offset = 0
                url = (
                    f"{base}/api/themes?q={urllib.parse.quote(query)}"
                    f"&limit={limit}&offset={offset}"
                )
                result = await asyncio.to_thread(self._http_sync, url, "GET", token)
                for item in result.get("items") or []:
                    for key in ("cover_url", "download_url"):
                        if item.get(key) and not item[key].startswith("http"):
                            item[key] = base + item[key]
                return self._ok(**result, url=base)
            if action in {"like", "favorite"}:
                if not token:
                    return error_response("请先登录市场")
                theme_id = str(payload.get("id") or "").strip()
                result = await asyncio.to_thread(
                    self._http_sync,
                    f"{base}/api/themes/{theme_id}/{action}",
                    "POST",
                    token,
                    {},
                )
                return self._ok(**result, url=base)
            if action == "my":
                if not token:
                    return error_response("请先登录市场")
                kind = str(payload.get("kind") or "published")
                result = await asyncio.to_thread(
                    self._http_sync, f"{base}/api/me/{kind}", "GET", token
                )
                for item in result.get("items") or []:
                    for key in ("cover_url", "download_url"):
                        if item.get(key) and not item[key].startswith("http"):
                            item[key] = base + item[key]
                return self._ok(**result, url=base)
            if action == "download":
                theme_id = str(payload.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", theme_id):
                    return error_response("主题 id 无效")
                detail = await asyncio.to_thread(
                    self._http_sync, f"{base}/api/themes/{theme_id}", "GET", token
                )
                incoming_name = str(detail.get("name") or theme_id)
                existing = next(
                    (
                        item
                        for item in self._theme_list()
                        if item.get("id") == theme_id or item.get("name") == incoming_name
                    ),
                    None,
                )
                mode = str(payload.get("mode") or "ask")
                if existing and mode == "ask":
                    return self._ok(
                        conflict=True,
                        mode="ask",
                        existing={"id": existing["id"], "name": existing["name"]},
                        incoming={"id": theme_id, "name": incoming_name},
                    )
                if existing and mode == "cancel":
                    return self._ok(cancelled=True)
                if existing and mode == "overwrite":
                    shutil.rmtree(self.themes_dir / existing["id"], ignore_errors=True)
                    (self.themes_dir / f"{existing['id']}.zip").unlink(missing_ok=True)
                    (self.themes_dir / f"{existing['id']}.fishtheme").unlink(missing_ok=True)
                self.market_cancel = False
                self.market_progress.update(
                    active=True, phase="准备下载", received=0,
                    total=int(detail.get("size") or 0), error=""
                )
                try:
                    raw = await asyncio.to_thread(
                        self._download_bytes,
                        f"{base}/api/themes/{theme_id}/download",
                        token,
                        detail.get("size") or 0,
                    )
                except Exception as exc:
                    self.market_progress.update(active=False, phase="下载失败", error=str(exc))
                    raise
                if not isinstance(raw, (bytes, bytearray)):
                    self.market_progress.update(active=False, phase="下载失败", error="服务器没有返回文件")
                    return error_response("下载失败：服务器没有返回文件")
                temp = self.themes_dir / f"market_{uuid.uuid4().hex[:8]}.zip"
                temp.write_bytes(raw)
                try:
                    installed = await asyncio.to_thread(
                        theme_store.install_theme, temp, self.themes_dir
                    )
                finally:
                    temp.unlink(missing_ok=True)
                if existing and mode == "rename":
                    theme_file = self.themes_dir / installed["id"] / "theme.json"
                    theme = theme_store.read_theme_json(theme_file)
                    suffix = installed["id"][:4]
                    new_name = str(payload.get("name") or "").strip()[:60]
                    theme["meta"]["name"] = new_name or f"{incoming_name} ({suffix})"
                    theme_file.write_text(
                        json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                applied = False
                if bool(payload.get("apply")):
                    applied = await self._apply_theme_id(installed["id"])
                    if not applied:
                        self.market_progress.update(
                            active=False, phase="应用失败",
                            error="主题已下载，但画布没有可播放的循环节点"
                        )
                        return error_response("主题已下载，但应用失败：画布缺少可播放节点")
                self.market_progress.update(
                    active=False, phase="已安装",
                    received=int(self.market_progress.get("received") or 0),
                    total=int(self.market_progress.get("total") or 0), error=""
                )
                return self._ok(
                    theme_id=installed["id"],
                    theme=installed["theme"]["meta"],
                    themes=self._theme_list(),
                    **({"applied": True} if applied else {}),
                )
            if action == "upload":
                if not token:
                    return error_response("请先登录市场")
                project_id = str(payload.get("project_id") or "")
                if project_id:
                    project = self._project_dir(project_id)
                    if not project:
                        return error_response("项目不存在")
                    project_theme = theme_store.read_theme_json(project / "theme.json")
                    snapshot = project_theme.get("config") or {}
                    meta = project_theme.get("meta") or self.theme_meta
                    video_dir = project / "videos"
                    cover = self._project_cover(project_id)
                else:
                    snapshot = self._theme_snapshot()
                    meta = self.theme_meta
                    video_dir = self.video_dir
                    cover = self._project_cover(self.active_theme_id)
                zip_path, theme = await asyncio.to_thread(
                    theme_store.build_theme,
                    snapshot,
                    meta,
                    self.themes_dir,
                    video_dir,
                    True,
                    cover,
                )
                try:
                    raw = zip_path.read_bytes()
                    multipart = self._multipart_file(zip_path.name, raw)
                    result = await asyncio.to_thread(
                        self._http_sync,
                        f"{base}/api/themes",
                        "POST",
                        token,
                        None,
                        multipart,
                        120.0,
                    )
                finally:
                    zip_path.unlink(missing_ok=True)
                return self._ok(item=result, url=base)
            if action == "update_info":
                if not token:
                    return error_response("请先登录市场")
                theme_id = str(payload.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", theme_id):
                    return error_response("市场主题 id 不合法")
                fields = {
                    "name": str(payload.get("name") or "").strip()[:60],
                    "description": str(payload.get("description") or "").strip()[:2000],
                    "version": str(payload.get("version") or "").strip()[:32],
                }
                item = await asyncio.to_thread(
                    self._http_sync,
                    f"{base}/api/themes/{theme_id}",
                    "PATCH",
                    token,
                    fields,
                )
                return self._ok(item=item, url=base)
            if action == "update_market":
                if not token:
                    return error_response("请先登录市场")
                theme_id = str(payload.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", theme_id):
                    return error_response("市场主题 id 不合法")
                project_id = str(payload.get("project_id") or "")
                if not project_id:
                    return error_response("请选择要更新的项目")
                project = self._project_dir(project_id)
                if not project:
                    return error_response("项目不存在")
                project_theme = theme_store.read_theme_json(project / "theme.json")
                zip_path, _ = await asyncio.to_thread(
                    theme_store.build_theme,
                    project_theme.get("config") or {},
                    project_theme.get("meta") or self.theme_meta,
                    self.themes_dir,
                    project / "videos",
                    True,
                    self._project_cover(project_id),
                )
                try:
                    raw = zip_path.read_bytes()
                    multipart = self._multipart_file(zip_path.name, raw)
                    item = await asyncio.to_thread(
                        self._http_sync,
                        f"{base}/api/themes/{theme_id}/file",
                        "POST",
                        token,
                        None,
                        multipart,
                        120.0,
                    )
                finally:
                    zip_path.unlink(missing_ok=True)
                return self._ok(item=item, url=base)
            if action == "delete_market":
                if not token:
                    return error_response("请先登录市场")
                theme_id = str(payload.get("id") or "").strip()
                result = await asyncio.to_thread(
                    self._http_sync, f"{base}/api/themes/{theme_id}", "DELETE", token
                )
                return self._ok(**result)
            return error_response("未知操作")
        except Exception as exc:
            return error_response(str(exc))

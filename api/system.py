"""概览 / 状态 / 事件 / 设置 / 同步 / 备份接口。"""

from __future__ import annotations

import json
import shutil

from astrbot.api import logger
from astrbot.api.web import error_response, request

from ..core.constants import ALLOWED_EXT, VERSION


class SystemApiMixin:
    def _overview(self) -> dict:
        return {
            "version": VERSION,
            "state": dict(self.state),
            "flow": {
                "nodes": [dict(item) for item in self.flow.get("nodes", [])],
                "edges": [dict(item) for item in self.flow.get("edges", [])],
            },
            "library": [dict(item) for item in self.library],
            "theme_meta": dict(self.theme_meta),
            "active_theme_id": self.active_theme_id,
            "themes": self._theme_list(),
            "theme_market_url": self.theme_market_url,
            "settings": dict(self.settings),
            "public_url": self._public_url(),
            "export_url": self._export_url(),
            "panel_url": self._panel_url(),
            "public_ready": self.server.running,
            "prefix": self._prefix(),
        }

    async def api_overview(self):
        await self._sync_public()
        return self._ok(**self._overview())

    async def api_state(self):
        return self._ok(state=dict(self.state))

    async def api_event(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        if action in {"bubble", "气泡", "activity"}:
            text = str(payload.get("text") or payload.get("activity") or "").strip()
            try:
                ttl = int(payload.get("ttl_seconds") or 0)
            except (TypeError, ValueError):
                ttl = 0
            await self.set_bubble(text, ttl)
        elif action in {"flow_goto", "节点"}:
            node_id = str(payload.get("node_id") or payload.get("name") or "")
            if not await self.flow_goto(node_id):
                return error_response("节点不存在")
        elif action in {"reset", "初始化", "rest", "休息"}:
            await self.force_rest()
        else:
            return error_response("未知动作")
        return self._ok(state=dict(self.state))

    async def api_settings(self):
        payload = await request.json(default={})
        if "theme_market_url" in payload:
            market_url = str(payload.get("theme_market_url") or "").strip().rstrip("/")
            if not market_url.startswith(("http://", "https://")):
                return error_response("市场地址必须是 http/https")
            self.theme_market_url = market_url or "https://shop.swellengine.com/"
        if "crossfade_ms" in payload:
            try:
                value = int(payload["crossfade_ms"])
            except (TypeError, ValueError):
                value = int(self.settings.get("crossfade_ms", 400))
            self.settings["crossfade_ms"] = max(0, min(3000, value))
        if "bubble_enabled" in payload:
            self.settings["bubble_enabled"] = 1 if payload.get("bubble_enabled") else 0
        self._write_config()
        await self._sync_public()
        return self._ok(
            settings=dict(self.settings),
            theme_market_url=self.theme_market_url,
        )

    async def api_sync(self):
        await self._sync_public()
        return self._ok(
            public_url=self._public_url(),
            public_ready=bool(self.public_dir and (self.public_dir / "index.html").is_file()),
        )

    async def api_backups(self):
        backups = []
        if self.backup_dir.is_dir():
            for folder in sorted(
                [p for p in self.backup_dir.iterdir() if p.is_dir()],
                key=lambda p: p.name,
                reverse=True,
            ):
                meta = {}
                try:
                    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                except Exception:
                    pass
                videos = []
                video_dir = folder / "videos"
                if video_dir.is_dir():
                    videos = [p.name for p in video_dir.glob("*") if p.is_file()]
                backups.append(
                    {
                        "id": folder.name,
                        "created_at": meta.get("created_at") or 0,
                        "reason": meta.get("reason") or "",
                        "videos": videos,
                        "has_config": (folder / "config.json").is_file(),
                    }
                )
        return self._ok(backups=backups)

    async def api_restore(self):
        payload = await request.json(default={})
        backup_id = str(payload.get("id") or "").strip()
        if not backup_id or "/" in backup_id or "\\" in backup_id or backup_id.startswith("."):
            return error_response("备份 ID 不正确")
        folder = (self.backup_dir / backup_id).resolve()
        try:
            folder.relative_to(self.backup_dir.resolve())
        except ValueError:
            return error_response("备份路径不正确")
        if not folder.is_dir():
            return error_response("备份不存在")
        self._backup_data("before-restore")
        try:
            backup_videos = folder / "videos"
            if backup_videos.is_dir():
                for row in backup_videos.glob("*"):
                    if row.is_file() and row.suffix.lower() in ALLOWED_EXT:
                        shutil.copy2(row, self.video_dir / row.name)
            config_backup = folder / "config.json"
            if config_backup.is_file():
                shutil.copy2(config_backup, self.config_file)
            self._load()
            self._write_config()
            await self._sync_public()
            return self._ok(restored=backup_id, library=list(self.library))
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋恢复备份失败: {exc}")
            return error_response("恢复备份失败")

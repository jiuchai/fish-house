"""素材库 / 画布接口。"""

from __future__ import annotations

import uuid
from pathlib import Path

from astrbot.api.web import error_response, request


class ContentApiMixin:
    async def api_library(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        media_id = str(payload.get("id") or "").strip()
        if action == "add_url":
            url = str(payload.get("url") or "").strip()
            if not url.startswith(("http://", "https://", "/")):
                return error_response("地址必须是 http(s) 链接或 / 开头")
            item = {
                "id": f"m_{uuid.uuid4().hex[:8]}",
                "name": str(payload.get("name") or Path(url).stem)[:40],
                "video": url,
                "duration": max(0.0, float(payload.get("duration") or 0)),
            }
            self.library.append(item)
            self._write_config()
            return self._ok(library=self.library, flow=self.flow)
        item = self._library_item(media_id)
        if item is None:
            return error_response("素材不存在")
        if action == "rename":
            name = str(payload.get("name") or "").strip()
            if not name:
                return error_response("名字不能为空")
            item["name"] = name[:40]
            self._write_config()
        elif action == "delete":
            used_by_nodes = [
                str(node.get("label") or node.get("id"))
                for node in self.flow.get("nodes", [])
                if str(node.get("media_id") or "") == media_id
            ]
            if used_by_nodes:
                return error_response("这个素材还被画布节点使用：" + "、".join(used_by_nodes[:5]))
            self.library = [x for x in self.library if x.get("id") != media_id]
            self._remove_local_video(str(item.get("video") or ""))
            self._write_config()
        else:
            return error_response("未知操作")
        return self._ok(library=self.library, flow=self.flow)

    async def api_flow(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        if action == "save":
            raw = payload.get("flow") if isinstance(payload.get("flow"), dict) else payload
            flow = self._normalize_flow(raw)
            if not self._flow_has_loop(flow):
                return error_response("画布至少要保留一个循环播放节点")
            self.flow = flow
            self._ensure_library()
            self._write_config()
            await self._refresh_current_node()
        elif action == "generate":
            self.flow = self._seed_flow()
            self._ensure_library()
            self._write_config()
        elif action == "goto":
            node_id = str(payload.get("node_id") or "")
            if not await self.flow_goto(node_id, text=str(payload.get("text") or "")):
                return error_response("节点不存在")
        else:
            return error_response("未知操作")
        return self._ok(flow=self.flow, state=dict(self.state), library=self.library)

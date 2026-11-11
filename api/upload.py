"""视频 / 主题包 / 封面的分片上传接口。"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import error_response, request

from .. import theme_store
from ..core.constants import ALLOWED_EXT
from ..core.utils import _safe_name


class UploadApiMixin:
    async def api_upload_start(self):
        payload = await request.json(default={})
        node_id = str(payload.get("node_id") or "")
        to_library = bool(payload.get("library"))
        to_theme = bool(payload.get("theme"))
        target_kind = str(payload.get("target") or "")
        project_id = str(payload.get("project_id") or "")
        to_project_asset = target_kind == "project_asset"
        to_cover = target_kind == "theme_cover"
        filename = str(payload.get("filename") or "")
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if to_project_asset or to_cover:
            if self._project_dir(project_id) is None:
                return error_response("项目不存在")
        elif to_theme or to_library:
            pass
        elif node_id:
            if self._flow_node(node_id) is None:
                return error_response("未知流程图节点")
        else:
            return error_response("未知上传目标")
        ext = Path(filename).suffix.lower()
        if to_theme:
            if ext not in {".fishtheme", ".zip"}:
                return error_response("主题只支持 .fishtheme / .zip")
        elif to_cover:
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                return error_response("封面只支持 jpg / png / webp")
        elif ext not in ALLOWED_EXT:
            return error_response("只支持 mp4 / webm / m4v / mov 视频")
        max_bytes = int(self._cfg("max_upload_mb", 200)) * 1024 * 1024
        if size <= 0 or size > max_bytes:
            return error_response(f"文件大小需在 1 字节到 {max_bytes // 1024 // 1024}MB 之间")
        upload_id = uuid.uuid4().hex
        part = self.upload_dir / f"{upload_id}.part"
        part.write_bytes(b"")
        self._uploads[upload_id] = {
            "node_id": node_id,
            "library": to_library,
            "theme": to_theme,
            "project_asset": to_project_asset,
            "cover": to_cover,
            "project_id": project_id,
            "replace_id": str(payload.get("replace_id") or ""),
            "filename": Path(filename).stem,
            "ext": ext,
            "size": size,
            "received": 0,
            "index": 0,
            "part": part,
        }
        return self._ok(upload_id=upload_id, chunk_size=(512 if to_theme else 128) * 1024)

    async def api_upload_chunk(self):
        payload = await request.json(default={})
        upload_id = str(payload.get("upload_id") or "")
        info = self._uploads.get(upload_id)
        if not info:
            return error_response("上传会话不存在或已过期")
        try:
            index = int(payload.get("index") or 0)
            raw = base64.b64decode(str(payload.get("data") or ""), validate=False)
        except Exception:
            return error_response("分片数据格式错误")
        if index != info["index"]:
            return error_response("分片顺序错误")
        if info["received"] + len(raw) > info["size"]:
            return error_response("视频大小超出声明")
        with info["part"].open("ab") as handle:
            handle.write(raw)
        info["index"] += 1
        info["received"] += len(raw)
        return self._ok(received=info["received"], index=info["index"])

    async def api_upload_finish(self):
        payload = await request.json(default={})
        upload_id = str(payload.get("upload_id") or "")
        info = self._uploads.pop(upload_id, None)
        if not info:
            return error_response("上传会话不存在或已过期")
        try:
            if info["received"] <= 0:
                info["part"].unlink(missing_ok=True)
                return error_response("视频为空")
            if info["received"] != info["size"]:
                info["part"].unlink(missing_ok=True)
                return error_response("视频上传不完整，请重试")
            node_id = str(info.get("node_id") or "")
            if info.get("project_asset"):
                project = self._project_dir(str(info.get("project_id") or ""))
                if project is None:
                    info["part"].unlink(missing_ok=True)
                    return error_response("项目已删除")
                theme = theme_store.read_theme_json(project / "theme.json")
                # 文件名只用随机 id，不掺原始文件名，避免同名/同秒上传互相覆盖；
                # 展示名依旧存在素材的 name 字段里，和文件名一一对应。
                target = project / "videos" / f"asset_{uuid.uuid4().hex}{info['ext']}"
                target.parent.mkdir(parents=True, exist_ok=True)
                info["part"].replace(target)
                mid = f"m_{uuid.uuid4().hex[:8]}"
                item = {
                    "id": mid,
                    "name": str(info.get("filename") or Path(target.name).stem)[:40],
                    "video": f"videos/{target.name}",
                    "duration": await self._probe_duration(target),
                }
                library = theme.setdefault("config", {}).setdefault("library", [])
                replace_id = str(info.get("replace_id") or "")
                old = next((x for x in library if x.get("id") == replace_id), None) if replace_id else None
                if old is not None:
                    item["id"] = str(old.get("id") or mid)
                    item["name"] = str(old.get("name") or item["name"])[:40]
                    library[library.index(old)] = item
                    old_local = str(old.get("video") or "")
                    shared = any(
                        x is not old and str(x.get("video") or "") == old_local
                        for x in library
                    )
                    if old_local.startswith("videos/") and old_local != item["video"] and not shared:
                        (project / old_local).unlink(missing_ok=True)
                else:
                    library.append(item)
                (project / "theme.json").write_text(
                    json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return self._ok(project=self._project_payload(str(info.get("project_id"))))
            if info.get("cover"):
                project = self._project_dir(str(info.get("project_id") or ""))
                if project is None:
                    info["part"].unlink(missing_ok=True)
                    return error_response("项目已删除")
                theme = theme_store.read_theme_json(project / "theme.json")
                cover_name = f"cover{info['ext']}"
                info["part"].replace(project / cover_name)
                for other in project.glob("cover.*"):
                    if other.name != cover_name:
                        other.unlink(missing_ok=True)
                theme.setdefault("meta", {})["cover"] = cover_name
                (project / "theme.json").write_text(
                    json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return self._ok(project=self._project_payload(str(info.get("project_id"))))
            if info.get("theme"):
                target = self.themes_dir / f"upload_{int(time.time())}_{_safe_name(str(info.get('filename') or 'theme'))}.zip"
                info["part"].replace(target)
                try:
                    installed = theme_store.install_theme(target, self.themes_dir)
                finally:
                    target.unlink(missing_ok=True)
                return self._ok(
                    theme=installed["theme"]["meta"],
                    theme_id=installed["id"],
                    themes=theme_store.list_installed(self.themes_dir),
                )
            if info.get("library"):
                target = self.video_dir / f"media_{uuid.uuid4().hex}{info['ext']}"
                info["part"].replace(target)
                mid = f"m_{uuid.uuid4().hex[:8]}"
                item = {
                    "id": mid,
                    "name": str(info.get("filename") or Path(target.name).stem)[:40],
                    "video": f"videos/{target.name}",
                    "duration": await self._probe_duration(target),
                }
                self.library.append(item)
                self._write_config()
                return self._ok(library=self.library, item=item)
            if node_id:
                node = self._flow_node(node_id)
                if node is None:
                    info["part"].unlink(missing_ok=True)
                    return error_response("流程图节点已删除")
                target = self.video_dir / (
                    f"node_{_safe_name(node_id)}_{int(time.time())}_{uuid.uuid4().hex[:6]}{info['ext']}"
                )
                info["part"].replace(target)
                node["video"] = f"videos/{target.name}"
                node["duration"] = await self._probe_duration(target)
                self._write_config()
                return self._ok(
                    node_id=node_id,
                    url=node["video"],
                    duration=node["duration"],
                    flow=self.flow,
                )
            return error_response("未知上传目标")
        except Exception as exc:
            info["part"].unlink(missing_ok=True)
            logger.warning(f"大肥鱼的小屋保存视频失败: {exc}")
            return error_response("保存视频失败")

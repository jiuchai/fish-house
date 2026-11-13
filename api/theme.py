"""主题导入导出与主题项目接口。"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid

from astrbot.api.web import error_response, request

from .. import theme_store



class ThemeApiMixin:
    async def api_theme(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        if action == "export":
            meta = {
                "name": str(payload.get("name") or self.theme_meta.get("name") or "我的主题"),
                "author": str(payload.get("author") or self.theme_meta.get("author") or ""),
                "description": str(
                    payload.get("description") or self.theme_meta.get("description") or ""
                ),
                "version": str(payload.get("version") or self.theme_meta.get("version") or "1.0.0"),
            }
            include = bool(payload.get("include_videos", True))
            try:
                zip_path, theme = await asyncio.to_thread(
                    theme_store.build_theme,
                    self._theme_snapshot(),
                    meta,
                    self.themes_dir,
                    self.video_dir,
                    include,
                    self._project_cover(self.active_theme_id),
                )
            except Exception as exc:
                return error_response(f"导出失败：{exc}")
            url = ""
            if self.public_dir:
                target_dir = self.public_dir / "themes" / "exports"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{theme['meta']['id']}.zip"
                try:
                    shutil.copy2(zip_path, target)
                    url = f"{self._theme_base()}/themes/exports/{target.name}"
                except Exception:
                    url = ""
            return self._ok(url=url, theme=theme["meta"], filename=zip_path.name)
        if action == "apply":
            tid = str(payload.get("id") or "").strip()
            if not await self._apply_theme_id(tid):
                return error_response("主题不存在或应用失败")
            return self._ok(**self._overview())
        if action == "delete":
            tid = str(payload.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", tid):
                return error_response("主题 id 无效")
            theme_dir = self.themes_dir / tid
            if not theme_dir.is_dir():
                return error_response("主题不存在")
            shutil.rmtree(theme_dir, ignore_errors=True)
            (self.themes_dir / f"{tid}.zip").unlink(missing_ok=True)
            (self.themes_dir / f"{tid}.fishtheme").unlink(missing_ok=True)
            self._purge_theme_files(tid)
            return self._ok(themes=self._theme_list())
        if action == "meta":
            for key in ("name", "author", "description", "version"):
                if payload.get(key) is not None:
                    self.theme_meta[key] = str(payload[key])[:2000]
            self._write_config()
            return self._ok(theme_meta=dict(self.theme_meta))
        if action == "apply_url":
            # 直接应用一个本地 zip（面板导入后可选择立即应用）
            url = str(payload.get("url") or "")
            return error_response("请先导入再应用")
        return error_response("未知操作")

    async def api_theme_project(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        tid = str(payload.get("id") or "").strip()
        if action == "load":
            if not self._project_dir(tid):
                return error_response("项目不存在")
            return self._ok(project=self._project_payload(tid))
        if action == "create":
            name = str(payload.get("name") or "新主题")[:60]
            copy_from = str(payload.get("copy_from") or "")
            if copy_from:
                source = self._project_dir(copy_from)
                if not source:
                    return error_response("要复制的项目不存在")
                new_id = uuid.uuid4().hex[:8]
                target = self.themes_dir / new_id
                shutil.copytree(source, target)
                theme = theme_store.read_theme_json(target / "theme.json")
                theme["meta"]["id"] = new_id
                theme["meta"]["name"] = name
                theme["meta"]["created_at"] = time.time()
                (target / "theme.json").write_text(
                    json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return self._ok(project=self._project_payload(new_id))
            meta = {
                "name": name,
                "author": str(payload.get("author") or self.theme_meta.get("author") or ""),
                "description": str(payload.get("description") or "")[:2000],
                "version": "1.0.0",
            }
            snapshot = self._theme_snapshot()
            snapshot["actions"] = []
            snapshot["videos"] = {}
            snapshot["audio"] = {}
            snapshot["durations"] = {}
            snapshot["library"] = []
            snapshot["flow"] = {
                "nodes": [
                    {
                        "id": "wait_1",
                        "label": "循环等待",
                        "role": "wait",
                        "loop": True,
                        "video": "",
                        "media_id": "",
                        "prompt": "",
                        "duration": 0,
                        "x": 420,
                        "y": 220,
                    }
                ],
                "edges": [],
            }
            zip_path, theme = await asyncio.to_thread(
                theme_store.build_theme,
                snapshot,
                meta,
                self.themes_dir,
                self.video_dir,
                False,
                None,
            )
            installed = await asyncio.to_thread(theme_store.install_theme, zip_path, self.themes_dir)
            zip_path.unlink(missing_ok=True)
            return self._ok(project=self._project_payload(installed["id"]))
        if action in {"save", "duplicate"}:
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            theme = theme_store.read_theme_json(project / "theme.json")
            if action == "duplicate":
                new_id = uuid.uuid4().hex[:8]
                target = self.themes_dir / new_id
                shutil.copytree(project, target)
                theme["meta"]["id"] = new_id
                theme["meta"]["name"] = str(payload.get("name") or (theme["meta"].get("name", "") + " 副本"))[:60]
                theme["meta"]["created_at"] = time.time()
                (target / "theme.json").write_text(
                    json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return self._ok(project=self._project_payload(new_id))
            meta = theme.get("meta") or {}
            for key in ("name", "author", "description", "version"):
                if payload.get("meta", {}).get(key) is not None:
                    meta[key] = str(payload["meta"][key])[:2000]
            theme["meta"] = meta
            config = payload.get("config")
            if isinstance(config, dict):
                for key in ("actions", "videos", "audio", "durations", "library", "flow", "settings"):
                    if key in config:
                        theme["config"][key] = config[key]
            if not self._flow_has_loop(theme["config"].get("flow") or {}):
                return error_response("画布至少要保留一个循环播放节点")
            # 素材被“更换”后节点里可能还留着旧文件路径，保存时按 media_id 校正
            config = theme["config"]
            lib_map = {
                str(item.get("id")): str(item.get("video") or "")
                for item in config.get("library") or []
            }
            for node in (config.get("flow") or {}).get("nodes") or []:
                bound = lib_map.get(str(node.get("media_id") or ""))
                if bound:
                    node["video"] = bound
            (project / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if tid == self.active_theme_id:
                # 保存的是正在应用的主题：直接同步运行时画布与壁纸，不重置当前节点。
                await self._sync_active_theme(tid)
            return self._ok(project=self._project_payload(tid))
        if action == "asset_delete":
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            theme = theme_store.read_theme_json(project / "theme.json")
            media_id = str(payload.get("media_id") or "")
            config = theme.get("config") or {}
            item = next((x for x in config.get("library") or [] if x.get("id") == media_id), None)
            if item is None:
                return error_response("素材不存在")
            video = str(item.get("video") or "")
            used = [
                str(n.get("label") or n.get("id"))
                for n in (config.get("flow") or {}).get("nodes") or []
                if str(n.get("media_id") or "") == media_id
                or (video and str(n.get("video") or "") == video)
            ]
            if used:
                return error_response("素材还被节点使用：" + "、".join(used[:5]) + "，不能删除")
            config["library"] = [x for x in config.get("library") or [] if x.get("id") != media_id]
            theme["config"] = config
            (project / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            shared = any(str(x.get("video") or "") == video for x in config["library"])
            if video.startswith("videos/") and not shared:
                (project / video).unlink(missing_ok=True)
            return self._ok(project=self._project_payload(tid))
        if action == "asset_rename":
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            name = str(payload.get("name") or "").strip()
            if not name:
                return error_response("名字不能为空")
            theme = theme_store.read_theme_json(project / "theme.json")
            config = theme.get("config") or {}
            item = next(
                (x for x in config.get("library") or [] if x.get("id") == str(payload.get("media_id") or "")),
                None,
            )
            if item is None:
                return error_response("素材不存在")
            item["name"] = name[:40]
            theme["config"] = config
            (project / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return self._ok(project=self._project_payload(tid))
        if action == "export":
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            theme = theme_store.read_theme_json(project / "theme.json")
            try:
                zip_path, built = await asyncio.to_thread(
                    theme_store.build_theme,
                    theme.get("config") or {},
                    theme.get("meta") or self.theme_meta,
                    self.themes_dir,
                    project / "videos",
                    True,
                    self._project_cover(tid),
                )
            except Exception as exc:
                return error_response(f"导出失败：{exc}")
            url = ""
            if self.public_dir:
                target_dir = self.public_dir / "themes" / "exports"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{tid}.zip"
                try:
                    shutil.copy2(zip_path, target)
                    url = f"{self._theme_base()}/themes/exports/{target.name}"
                except Exception:
                    url = ""
            return self._ok(url=url, filename=zip_path.name, theme=built["meta"])
        if action == "delete":
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            shutil.rmtree(project, ignore_errors=True)
            (self.themes_dir / f"{tid}.zip").unlink(missing_ok=True)
            (self.themes_dir / f"{tid}.fishtheme").unlink(missing_ok=True)
            self._purge_theme_files(tid)
            if self.active_theme_id == tid:
                self.active_theme_id = ""
                self._write_config()
            return self._ok(themes=self._theme_list())
        return error_response("未知操作")

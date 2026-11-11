"""主题与主题项目（安装、应用、发布、会话）。"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .. import theme_store



class ThemeMixin:
    def _theme_base(self) -> str:
        return self._public_url().rsplit("/", 1)[0]

    def _theme_public_cover(self, tid: str, theme: dict) -> str:
        cover = str((theme.get("meta") or {}).get("cover") or "")
        if not cover or not self.public_dir:
            return ""
        source = self.themes_dir / tid / cover
        if not source.is_file():
            return ""
        target_dir = self.public_dir / "themes" / "covers"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{tid}{source.suffix.lower()}"
        try:
            for old in target_dir.glob(f"{tid}.*"):
                if old.name != target.name:
                    old.unlink(missing_ok=True)
            shutil.copy2(source, target)
        except Exception:
            return ""
        return f"{self._theme_base()}/themes/covers/{target.name}"

    def _theme_list(self) -> list[dict[str, Any]]:
        items = theme_store.list_installed(self.themes_dir)
        for item in items:
            try:
                theme = theme_store.read_theme_json(self.themes_dir / item["id"] / "theme.json")
            except Exception:
                theme = {}
            item["cover_url"] = self._theme_public_cover(item["id"], theme)
        return items

    def _theme_snapshot(self) -> dict[str, Any]:
        return {
            "library": [dict(x) for x in self.library],
            "flow": json.loads(json.dumps(self.flow)),
            "settings": {"crossfade_ms": int(self.settings.get("crossfade_ms", 400))},
        }

    async def _apply_theme_id(self, tid: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", tid or ""):
            return False
        theme_dir = self.themes_dir / tid
        theme_file = theme_dir / "theme.json"
        if not theme_file.is_file():
            return False
        theme = theme_store.read_theme_json(theme_file)
        config = await asyncio.to_thread(
            theme_store.materialize_theme, theme, theme_dir, self.video_dir, tid
        )
        self.library = config["library"]
        self.flow = config["flow"]
        if config["settings"].get("crossfade_ms") is not None:
            self.settings["crossfade_ms"] = int(config["settings"]["crossfade_ms"])
        self.active_theme_id = tid
        meta = theme.get("meta") or {}
        self.theme_meta = {
            "name": str(meta.get("name") or tid)[:60],
            "author": str(meta.get("author") or "")[:40],
            "description": str(meta.get("description") or "")[:2000],
            "version": str(meta.get("version") or "1.0.0")[:32],
        }
        self._ensure_library()
        self._write_config()
        self._prune_theme_videos()
        await self._sync_public()
        # 应用主题后直接从画布的第一个循环节点开始。
        start = self._start_node()
        if start and await self.flow_goto(str(start.get("id") or "")):
            return True
        await self.force_rest()
        return True

    def _prune_theme_videos(self) -> None:
        """清掉全局视频目录里不再被当前主题引用的旧副本。"""
        used: set[str] = set()
        for item in self.library:
            used.add(Path(str(item.get("video") or "")).name)
        for node in (self.flow.get("nodes") or []):
            used.add(Path(str(node.get("video") or "")).name)
        for row in self.video_dir.glob("theme_*"):
            if not row.is_file() or row.name in used:
                continue
            row.unlink(missing_ok=True)
            if self.public_dir:
                (self.public_dir / "videos" / row.name).unlink(missing_ok=True)

    def _purge_theme_files(self, tid: str) -> None:
        """删除主题/项目时，连同派生的视频副本、导出包、封面一起清掉。"""
        for row in self.video_dir.glob(f"theme_{tid}_*"):
            row.unlink(missing_ok=True)
            if self.public_dir:
                (self.public_dir / "videos" / row.name).unlink(missing_ok=True)
        if not self.public_dir:
            return
        (self.public_dir / "themes" / "exports" / f"{tid}.zip").unlink(missing_ok=True)
        shutil.rmtree(self.public_dir / "themes" / "projects" / tid, ignore_errors=True)
        for cover in (self.public_dir / "themes" / "covers").glob(f"{tid}.*"):
            cover.unlink(missing_ok=True)

    def _project_cover(self, tid: str) -> Path | None:
        path = self.themes_dir / str(tid or "")
        if not path.is_dir():
            return None
        for name in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
            candidate = path / name
            if candidate.is_file():
                return candidate
        return None

    def _project_dir(self, tid: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", tid or ""):
            return None
        path = self.themes_dir / tid
        return path if (path / "theme.json").is_file() else None

    def _hydrate_project_videos(self, tid: str, theme: dict[str, Any]) -> None:
        """项目包缺视频时，从全局视频目录或其它项目/公开目录补齐。"""
        project = self.themes_dir / tid
        config = theme.get("config") or {}
        refs: set[str] = set()
        for item in config.get("library") or []:
            refs.add(str(item.get("video") or ""))
        for value in (config.get("videos") or {}).values():
            refs.add(str(value or ""))
        for node in (config.get("flow") or {}).get("nodes") or []:
            refs.add(str(node.get("video") or ""))
        for rel in refs:
            rel = rel.replace("\\", "/").lstrip("./")
            if not rel.startswith("videos/") or ".." in rel.split("/"):
                continue
            target = project / rel
            if target.is_file():
                continue
            name = Path(rel).name
            candidates: list[Path] = []
            if self.video_dir:
                candidates.append(self.video_dir / name)
            if self.public_dir:
                candidates.extend((self.public_dir / "themes" / "projects").glob(f"*/videos/{name}"))
            candidates.extend(self.themes_dir.glob(f"*/videos/{name}"))
            for candidate in candidates:
                if candidate.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, target)
                    break

    def _publish_project(self, tid: str) -> str:
        if not self.public_dir:
            return ""
        source = self.themes_dir / tid
        target = self.public_dir / "themes" / "projects" / tid
        target.mkdir(parents=True, exist_ok=True)
        videos = source / "videos"
        if videos.is_dir():
            shutil.copytree(videos, target / "videos", dirs_exist_ok=True)
        for cover in source.glob("cover.*"):
            shutil.copy2(cover, target / cover.name)
        return f"{self._theme_base()}/themes/projects/{tid}"

    def _project_payload(self, tid: str) -> dict[str, Any]:
        theme_file = self.themes_dir / tid / "theme.json"
        theme = theme_store.read_theme_json(theme_file)
        config = theme.setdefault("config", {})
        changed = False
        self._hydrate_project_videos(tid, theme)
        flow = config.get("flow")
        if not isinstance(flow, dict) or not flow.get("nodes"):
            flow = self._seed_flow()
            config["flow"] = flow
            changed = True
        for node in flow.get("nodes") or []:
            if node.get("role") == "rest" and node.get("loop"):
                node["role"] = "wait"
                changed = True
        if changed:
            theme_file.write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        base = self._publish_project(tid)
        return {
            "id": tid,
            "theme": theme,
            "public_base": base,
            "active": tid == self.active_theme_id,
            "cover_url": self._theme_public_cover(tid, theme),
        }

    def _market_sessions(self) -> dict[str, Any]:
        path = self.themes_dir.parent / "market_sessions.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_market_sessions(self, data: dict[str, Any]) -> None:
        path = self.themes_dir.parent / "market_sessions.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

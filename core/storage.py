"""本地数据、公共页面同步、备份与导出。"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.web import request
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .. import theme_store
from .constants import (
    ALLOWED_EXT,
    DEFAULT_SETTINGS,
    EXPORT_NAME,
    PLUGIN_NAME,
    PLUGIN_ROOT,
)
from .utils import _safe_name



class StorageMixin:
    def _load(self) -> None:
        data: dict[str, Any] = {}
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"大肥鱼的小屋读取配置失败: {exc}")
        if not isinstance(data, dict):
            data = {}
        if isinstance(data.get("settings"), dict):
            self.settings.update(data["settings"])
        self.active_theme_id = str(data.get("active_theme_id") or "")[:80]
        saved_market = str(data.get("theme_market_url") or "").strip()
        cfg_market = str(self._cfg("theme_market_url", "https://shop.swellengine.com/")).strip()
        legacy_market = {"http://127.0.0.1:8077", "http://localhost:8077"}
        if saved_market:
            self.theme_market_url = saved_market
        elif cfg_market and cfg_market not in legacy_market:
            self.theme_market_url = cfg_market
        else:
            self.theme_market_url = "https://shop.swellengine.com/"
        if isinstance(data.get("theme_meta"), dict):
            for key in ("name", "author", "description", "version"):
                if data["theme_meta"].get(key) is not None:
                    self.theme_meta[key] = str(data["theme_meta"][key])[:2000]
        if "flow" in data:
            self.flow = self._normalize_flow(data.get("flow"))
        else:
            self.flow = self._seed_flow()
        for node in self.flow.get("nodes") or []:
            if node.get("role") == "rest" and node.get("loop"):
                node["role"] = "wait"
        self.library = self._normalize_library(data.get("library"))
        self._ensure_library()
        cleaned: dict[str, int] = {}
        for key, default in DEFAULT_SETTINGS.items():
            try:
                cleaned[key] = int(self.settings.get(key, default))
            except (TypeError, ValueError):
                cleaned[key] = default
        self.settings = cleaned
        self._restore_flow_state()
        if self.public_prefix_enabled():
            self._write_config()

    def _detect_public_root(self) -> None:
        candidates = [
            PLUGIN_ROOT.parents[2] / "astrbot" / "dashboard" / "dist",
            Path(get_astrbot_data_path()) / "dist",
        ]
        for candidate in candidates:
            if (candidate / "index.html").is_file():
                self.public_root = candidate
                self.public_dir = candidate / self._prefix()
                break
        if self.public_dir:
            self.public_dir.mkdir(parents=True, exist_ok=True)

    def _public_url(self) -> str:
        host = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or "127.0.0.1:6185"
        )
        scheme = request.headers.get("x-forwarded-proto") or getattr(request, "scheme", "http")
        root_path = str(request.headers.get("x-forwarded-prefix") or "").strip("/")
        if not root_path:
            referer = str(request.headers.get("referer") or "")
            path = urlparse(referer).path if referer else ""
            marker = "/api/v1/plugins/"
            index = path.find(marker)
            if index > 0:
                root_path = path[:index].strip("/")
        root_path = f"/{root_path}" if root_path else ""
        return f"{scheme}://{host}{root_path}/{self._prefix()}/index.html"

    def _panel_url(self) -> str:
        """控制台地址：沿用访问者的主机名，端口换成插件自己的服务端口。"""
        host = str(
            request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or "127.0.0.1:6185"
        )
        host = host.split(",")[0].strip()
        if host.startswith("["):
            hostname = host.split("]")[0] + "]"
        else:
            hostname = host.split(":")[0]
        scheme = request.headers.get("x-forwarded-proto") or getattr(request, "scheme", "http")
        return f"{scheme}://{hostname}:{self.server_port}/"

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        # 公共目录的文件可能正被浏览器/静态服务读取，Windows 下替换会短暂报
        # “拒绝访问”（WinError 5）。重试几次；实在不行就直接覆盖原文件。
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.03)
        try:
            path.write_text(payload, encoding="utf-8")
        except OSError:
            if last_error is not None:
                raise last_error
            raise

    def _public_config(self) -> dict:
        return {
            "crossfade_ms": int(self.settings.get("crossfade_ms", 400)),
            "show_bubble": bool(int(self.settings.get("bubble_enabled", 1) or 0)),
            "title": "大肥鱼的小屋",
            "updated_at": time.time(),
        }

    def _write_config(self) -> None:
        self._write_json(
            self.config_file,
            {
                "flow": {
                    "nodes": [dict(item) for item in self.flow.get("nodes", [])],
                    "edges": [dict(item) for item in self.flow.get("edges", [])],
                },
                "library": [dict(item) for item in self.library],
                "theme_meta": dict(self.theme_meta),
                "active_theme_id": self.active_theme_id,
                "theme_market_url": self.theme_market_url,
                "settings": dict(self.settings),
            },
        )

    def _write_state_locked(self) -> None:
        self.state["updated_at"] = time.time()
        self._write_json(self.state_file, self.state)
        if self.public_dir:
            try:
                self._write_json(self.public_dir / "state.json", self.state)
            except OSError as exc:
                # 公共页文件被占用时不要让状态机跟着失败，下一次写入会补上。
                logger.warning(f"大肥鱼的小屋写公共状态失败: {exc}")
        self.server.publish_state(dict(self.state))

    async def _sync_public(self) -> None:
        self.server.publish_config(self._public_config())
        if not self.public_dir:
            return
        try:
            self.public_dir.mkdir(parents=True, exist_ok=True)
            live_dir = PLUGIN_ROOT / "web" / "live"
            for name in ("index.html", "app.js", "style.css"):
                source = live_dir / name
                if source.is_file():
                    shutil.copy2(source, self.public_dir / name)
            videos_public = self.public_dir / "videos"
            videos_public.mkdir(parents=True, exist_ok=True)
            keep: set[str] = set()
            for row in self.video_dir.glob("*"):
                if row.is_file() and row.suffix.lower() in ALLOWED_EXT:
                    keep.add(row.name)
                    target = videos_public / row.name
                    try:
                        if target.is_file() and target.stat().st_size == row.stat().st_size:
                            continue
                    except OSError:
                        pass
                    shutil.copy2(row, target)
            # 本地已经删掉的视频，公共目录也一起清掉，避免越积越多
            for row in videos_public.glob("*"):
                if row.is_file() and row.name not in keep:
                    row.unlink(missing_ok=True)
            self._write_json(self.public_dir / "config.json", self._public_config())
            async with self._lock:
                self._write_state_locked()
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋同步公共页面失败: {exc}")

    def _backup_data(self, reason: str = "auto") -> str | None:
        """把配置和视频复制到 backups/，更新数据前调用。"""
        has_videos = any(
            row.is_file() and row.suffix.lower() in ALLOWED_EXT
            for row in self.video_dir.glob("*")
        )
        if not self.config_file.exists() and not has_videos:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{_safe_name(reason, 'backup')}"
        dest = self.backup_dir / stamp
        suffix = 1
        while dest.exists():
            suffix += 1
            dest = self.backup_dir / f"{stamp}-{suffix}"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for source in (self.config_file, self.state_file):
                if source.exists():
                    shutil.copy2(source, dest / source.name)
            videos_dest = dest / "videos"
            videos_dest.mkdir(parents=True, exist_ok=True)
            copied = []
            for row in self.video_dir.glob("*"):
                if row.is_file() and row.suffix.lower() in ALLOWED_EXT:
                    shutil.copy2(row, videos_dest / row.name)
                    copied.append(row.name)
            self._write_json(
                dest / "meta.json",
                {
                    "created_at": time.time(),
                    "reason": reason,
                    "videos": copied,
                    "settings": dict(self.settings),
                },
            )
            backups = sorted(
                [p for p in self.backup_dir.iterdir() if p.is_dir()],
                key=lambda p: p.name,
                reverse=True,
            )
            for old in backups[20:]:
                shutil.rmtree(old, ignore_errors=True)
            return dest.name
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋备份失败: {exc}")
            return None

    def _build_export_zip(self) -> None:
        """把插件源码打包成可导入其他 AstrBot 的 zip，不包含视频和用户配置。"""
        if not self.public_dir:
            return
        root = PLUGIN_ROOT
        target = self.public_dir / EXPORT_NAME
        tmp = target.with_suffix(".tmp.zip")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(root)
                    if any(
                        part in {"__pycache__", "data", "backups", "videos", "uploads", "node_modules", ".git"}
                        for part in rel.parts
                    ):
                        continue
                    # 前端源码、默认主题包里的视频、依赖目录都不进插件包
                    if rel.parts[0] in {"assets", "frontend", "fish-house-theme-server"}:
                        continue
                    if path.name != "LICENSE" and path.suffix not in {".py", ".yaml", ".yml", ".json", ".md", ".html", ".js", ".css", ".zip", ".fishtheme"}:
                        continue
                    archive.write(path, f"{PLUGIN_NAME}/{rel.as_posix()}")
            tmp.replace(target)
            self.export_file = target
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋导出插件失败: {exc}")

    def _export_url(self) -> str:
        base = self._public_url()
        return base.rsplit("/", 1)[0] + "/" + EXPORT_NAME

    async def _install_default_theme(self) -> bool:
        # 新版本用 .zip；旧包名 .fishtheme 仍兼容
        asset = PLUGIN_ROOT / "assets" / "default_theme.zip"
        if not asset.is_file():
            asset = PLUGIN_ROOT / "assets" / "default_theme.fishtheme"
        if not asset.is_file():
            return False
        try:
            installed = await asyncio.to_thread(theme_store.install_theme, asset, self.themes_dir)
            if not await self._apply_theme_id(installed["id"]):
                return False
            logger.info(f"大肥鱼的小屋已应用默认主题：{installed['id']}")
            return True
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋应用默认主题失败: {exc}")
            return False

    async def initialize(self) -> None:
        self._backup_data("before-update")
        first_run = not int(self.settings.get("defaults_applied") or 0)
        if first_run:
            await self._install_default_theme()
            self.settings["defaults_applied"] = 1
            self._write_config()
        await self._ensure_durations()
        await self._sync_public()
        self._build_export_zip()
        if not self.state.get("node_id"):
            await self.force_rest()
        # 重启后如果恢复在循环工作节点，之前没有走 flow_goto，得补一个空闲监视，
        # 否则停在「循环工作」上永远不超时，也不回待机。
        node = self._flow_node(str(self.state.get("node_id") or ""))
        if node is not None and node.get("loop") and node.get("role") in {"work", "both"}:
            self._schedule_idle(str(node.get("id") or ""))
        self.server.publish_state(dict(self.state))
        await self.server.start(self.server_port)
        self._write_panel_port()
        logger.info(f"大肥鱼的小屋已加载，控制台端口：{self.server_port}")

    def _write_panel_port(self) -> None:
        """把控制台端口写进插件页目录，WebUI 跳转页启动时读它，改端口不必改页面。"""
        try:
            self._write_json(
                PLUGIN_ROOT / "pages" / "panel" / "port.json",
                {"port": int(self.server_port or 0)},
            )
        except OSError as exc:
            logger.warning(f"大肥鱼的小屋写入面板端口失败: {exc}")

    async def terminate(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        await self.server.stop()

    def _remove_local_video(self, url: str) -> None:
        if not url or url.startswith(("http://", "https://", "/")):
            return
        if url.startswith("videos/"):
            name = Path(url).name
            for folder in (self.video_dir, self.public_dir / "videos" if self.public_dir else None):
                if folder:
                    try:
                        (folder / name).unlink(missing_ok=True)
                    except Exception:
                        pass

    async def _probe_duration(self, path: Path) -> float:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        try:
            proc = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            return max(0.0, float(out.decode().strip() or 0))
        except Exception:
            return 0.0

    async def _ensure_durations(self) -> None:
        changed = False
        for item in self.library:
            url = str(item.get("video") or "")
            if not url.startswith("videos/"):
                continue
            if float(item.get("duration") or 0) > 0:
                continue
            path = self.video_dir / Path(url).name
            if path.is_file():
                item["duration"] = await self._probe_duration(path)
                changed = True
        if changed:
            self._write_config()

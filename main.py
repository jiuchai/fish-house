"""大肥鱼的小屋：视频桌宠 + 状态机 + 公共页面。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import theme_store

from astrbot.api import logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

PLUGIN_NAME = "astrbot_plugin_fish_house"
VERSION = "2.0.0"

SLOTS = [
    ("rest", "休息循环", "啥也不干时循环播放"),
    ("phone_pickup", "拿起手机", "收到消息，伸手拿手机"),
    ("phone_loop", "玩手机 / 回消息", "看手机、回消息时循环"),
    ("phone_putdown", "放下手机", "回复完，把手机放回去"),
    ("computer_go", "去电脑", "从休息处走到电脑前"),
    ("computer_loop", "玩电脑 / 干活", "干活时循环"),
    ("computer_rest", "电脑回休息", "干完活，从电脑回到休息"),
]
SLOT_KEYS = {key for key, _label, _hint in SLOTS}
SLOT_LABELS = {key: label for key, label, _hint in SLOTS}

# 动作表：7 个基础动作；以后可以在面板里继续加。
DEFAULT_ACTIONS: list[dict[str, Any]] = [
    {"key": key, "label": label, "hint": hint, "loop": key in {"rest", "phone_loop", "computer_loop"}}
    for key, label, hint in SLOTS
]
DEFAULT_ACTION_KEYS = {item["key"] for item in DEFAULT_ACTIONS}

ALLOWED_EXT = {".mp4", ".webm", ".m4v", ".mov"}
EXPORT_NAME = "astrbot_plugin_fish_house.zip"

# 自带一套默认视频：第一次加载时装进数据目录并填满 7 个槽位。
BUILTIN_VIDEOS: dict[str, tuple[str, float]] = {
    "rest": ("rest.mp4", 12.096),
    "phone_pickup": ("phone_pickup.mp4", 4.064),
    "phone_loop": ("phone_loop.mp4", 8.064),
    "phone_putdown": ("phone_putdown.mp4", 3.042),
    "computer_go": ("computer_go.mp4", 5.175),
    "computer_loop": ("computer_loop.mp4", 15.072),
    "computer_rest": ("computer_rest.mp4", 10.08),
}

DEFAULT_SETTINGS = {
    "crossfade_ms": 400,
    "auto_reply": 1,
    "defaults_applied": 0,
}


def _safe_name(text: str, fallback: str = "video") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", (text or "").strip())
    return text[:80] or fallback


class FishHousePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.video_dir = self.data_dir / "videos"
        self.upload_dir = self.data_dir / "uploads"
        for directory in (self.data_dir, self.video_dir, self.upload_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.config_file = self.data_dir / "config.json"
        self.state_file = self.data_dir / "state.json"
        self.backup_dir = self.data_dir / "backups"
        self.assets_dir = Path(__file__).resolve().parent / "assets" / "videos"
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._uploads: dict[str, dict[str, Any]] = {}
        self._idle_task: asyncio.Task | None = None
        self._guard_task: asyncio.Task | None = None
        self._stage_token = 0
        self._go_token = 0
        self._last_activity = ""
        self.videos: dict[str, str] = {}
        self.durations: dict[str, float] = {}
        self.audio: dict[str, bool] = {key: True for key in SLOT_KEYS}
        self.actions: list[dict[str, Any]] = [dict(item) for item in DEFAULT_ACTIONS]
        self.flow: dict[str, Any] = {"nodes": [], "edges": []}
        self.library: list[dict[str, Any]] = []
        self.themes_dir = self.data_dir / "themes"
        self.themes_dir.mkdir(parents=True, exist_ok=True)
        self.active_theme_id = ""
        self.theme_market_url = "https://shop.swellengine.com/"
        self.theme_meta: dict[str, Any] = {
            "name": "我的主题",
            "author": "",
            "description": "",
            "version": "1.0.0",
        }
        self.settings = dict(DEFAULT_SETTINGS)
        self.state = {
            "mode": "rest",
            "seq": 1,
            "updated_at": time.time(),
            "phone_until": 0,
            "event": "init",
            "work": False,
            "auto_work": False,
            "bubble": "",
            "bubble_until": 0,
            "bubble_source": "",
            "after_phone": "",
            "idle_until": 0,
            "task_done": False,
            "arrive_at": 0,
            "action_key": "",
            "action_loop": False,
            "node_id": "",
            "node_video": "",
            "node_loop": False,
            "node_label": "",
        }
        self.public_root: Path | None = None
        self.public_dir: Path | None = None
        self.export_file: Path | None = None
        self._load()
        self._detect_public_root()
        self._register_web_apis()

    # ------------------------------------------------------------------ 基础

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default) if self.config is not None else default
        except Exception:
            value = default
        return default if value is None else value

    def _can_feedback(self, event: AstrMessageEvent) -> bool:
        if not bool(self._cfg("admin_only", False)):
            return True
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    # ------------------------------------------------------------------ 动作

    def _action_keys(self) -> list[str]:
        return [str(item.get("key") or "") for item in self.actions if item.get("key")]

    def _action_def(self, key: str) -> dict[str, Any]:
        for item in self.actions:
            if item.get("key") == key:
                return item
        return {"key": key, "label": key, "hint": "", "loop": False}

    def _sync_action_slots(self) -> None:
        """给当前视频表补齐新动作的默认有声，并清掉已删除动作的槽位。"""
        keys = self._action_keys()
        for key in keys:
            self.audio.setdefault(key, True)
        for key in list(self.videos):
            if key not in keys:
                self.videos.pop(key, None)
        for key in list(self.durations):
            if key not in keys:
                self.durations.pop(key, None)
        for key in list(self.audio):
            if key not in keys:
                self.audio.pop(key, None)

    @staticmethod
    def _flow_has_loop(flow: dict[str, Any]) -> bool:
        return any(
            node.get("role") in {"wait", "both", "work", "phone"} and node.get("loop")
            for node in (flow.get("nodes") or [])
        )

    def _ensure_task_graph(self, flow: dict[str, Any]) -> None:
        nodes = flow.setdefault("nodes", [])
        edges = flow.setdefault("edges", [])
        for node in nodes:
            if node.get("id") == "phone_loop" and "玩手机" in str(node.get("label") or ""):
                node["label"] = "回消息"
            if node.get("id") == "computer_loop" and node.get("role") == "work":
                node["role"] = "both"
        if not any(n.get("id") == "phone_putdown" for n in nodes):
            return
        if not any(n.get("id") == "computer_go" for n in nodes):
            return
        if any(
            e.get("from") == "phone_putdown" and e.get("to") == "computer_go"
            for e in edges
        ):
            return
        edges.append(
            {
                "id": "e_put_computer",
                "from": "phone_putdown",
                "to": "computer_go",
                "label": "去电脑干活",
            }
        )

    def _ensure_wait_edges(self) -> None:
        """循环等待节点没有出边时，默认连到「收到消息」。"""
        nodes = self.flow.get("nodes") or []
        pickup = self._flow_node("phone_pickup")
        if pickup is None:
            pickup = next((n for n in nodes if n.get("role") == "phone"), None)
        if pickup is None:
            return
        edges = self.flow.setdefault("edges", [])
        for node in nodes:
            if node.get("role") != "wait":
                continue
            existing = next((e for e in edges if e.get("from") == node.get("id")), None)
            if existing is not None:
                if existing.get("to") == pickup.get("id") and existing.get("label") == "收到消息":
                    existing["label"] = "提前收到消息"
                continue
            edges.append(
                {
                    "id": f"e_{node.get('id')}_msg",
                    "from": node.get("id"),
                    "to": pickup.get("id"),
                    "label": "提前收到消息",
                }
            )

    def _load(self) -> None:
        data: dict[str, Any] = {}
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"大肥鱼的小屋读取配置失败: {exc}")
        if isinstance(data.get("actions"), list) and data["actions"]:
            actions: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in data["actions"]:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                if (
                    not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key)
                    or key in seen
                    or key == "change"
                ):
                    continue
                seen.add(key)
                actions.append(
                    {
                        "key": key,
                        "label": str(item.get("label") or key)[:40],
                        "hint": str(item.get("hint") or "")[:120],
                        "loop": bool(item.get("loop", False)),
                    }
                )
            if actions:
                self.actions = actions
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
        # 套装模块已删除：读取旧配置里的当前套装，迁移成单一的 videos/audio/durations。
        outfits_raw = data.get("outfits")
        source_slots: dict[str, Any] = {}
        if isinstance(outfits_raw, dict) and outfits_raw:
            current = str(data.get("current_outfit") or "default")
            picked = outfits_raw.get(current) or outfits_raw.get("default")
            if not isinstance(picked, dict):
                picked = next(
                    (item for item in outfits_raw.values() if isinstance(item, dict)), {}
                )
            source_slots = picked
        else:
            source_slots = {
                "videos": data.get("videos") if isinstance(data.get("videos"), dict) else {},
                "audio": data.get("audio") if isinstance(data.get("audio"), dict) else {},
                "durations": data.get("durations") if isinstance(data.get("durations"), dict) else {},
            }
        keys = self._action_keys()
        videos = source_slots.get("videos") if isinstance(source_slots.get("videos"), dict) else {}
        audio = source_slots.get("audio") if isinstance(source_slots.get("audio"), dict) else {}
        durations = source_slots.get("durations") if isinstance(source_slots.get("durations"), dict) else {}
        self.videos = {
            str(key): str(value)
            for key, value in videos.items()
            if value and str(key) in keys
        }
        self.audio = {key: bool(audio.get(key, True)) for key in keys}
        self.durations = {
            str(key): float(value or 0)
            for key, value in durations.items()
            if str(key) in keys
        }
        self._sync_action_slots()
        if "flow" in data:
            self.flow = self._normalize_flow(data.get("flow"))
        else:
            self.flow = self._seed_flow()
        for node in self.flow.get("nodes") or []:
            if node.get("role") == "rest" and node.get("loop"):
                node["role"] = "wait"
        self._ensure_task_graph(self.flow)
        self._ensure_wait_edges()
        if "library" in data:
            self.library = self._normalize_library(data.get("library"))
        self._ensure_library()
        cleaned: dict[str, int] = {}
        for key, default in DEFAULT_SETTINGS.items():
            try:
                cleaned[key] = int(self.settings.get(key, default))
            except (TypeError, ValueError):
                cleaned[key] = default
        self.settings = cleaned
        if self.public_prefix_enabled():
            self._write_config()

    def public_prefix_enabled(self) -> bool:
        return bool(str(self._cfg("enabled", True)).lower() not in {"0", "false", "off", "no"})

    def _prefix(self) -> str:
        prefix = str(self._cfg("public_prefix", "fish_house") or "fish_house").strip("/")
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix) or "fish_house"

    def _detect_public_root(self) -> None:
        candidates = [
            Path(__file__).resolve().parents[3] / "astrbot" / "dashboard" / "dist",
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
            "videos": dict(self.videos),
            "audio": {key: bool(self.audio.get(key, True)) for key in self._action_keys()},
            "actions": [dict(item) for item in self.actions],
            "crossfade_ms": int(self.settings.get("crossfade_ms", 400)),
            "title": "大肥鱼的小屋",
            "updated_at": time.time(),
        }

    def _write_config(self) -> None:
        self._write_json(
            self.config_file,
            {
                "actions": [dict(item) for item in self.actions],
                "flow": {
                    "nodes": [dict(item) for item in self.flow.get("nodes", [])],
                    "edges": [dict(item) for item in self.flow.get("edges", [])],
                },
                "library": [dict(item) for item in self.library],
                "theme_meta": dict(self.theme_meta),
                "active_theme_id": self.active_theme_id,
                "theme_market_url": self.theme_market_url,
                "videos": dict(self.videos),
                "audio": {
                    key: bool(self.audio.get(key, True)) for key in self._action_keys()
                },
                "durations": dict(self.durations),
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

    async def _sync_public(self) -> None:
        if not self.public_dir:
            return
        try:
            self.public_dir.mkdir(parents=True, exist_ok=True)
            live_dir = Path(__file__).resolve().parent / "web" / "live"
            for name in ("index.html", "app.js", "style.css"):
                source = live_dir / name
                if source.is_file():
                    shutil.copy2(source, self.public_dir / name)
            videos_public = self.public_dir / "videos"
            videos_public.mkdir(parents=True, exist_ok=True)
            for row in self.video_dir.glob("*"):
                if row.is_file() and row.suffix.lower() in ALLOWED_EXT:
                    target = videos_public / row.name
                    try:
                        if target.is_file() and target.stat().st_size == row.stat().st_size:
                            continue
                    except OSError:
                        pass
                    shutil.copy2(row, target)
            self._write_json(self.public_dir / "config.json", self._public_config())
            async with self._lock:
                self._write_state_locked()
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋同步公共页面失败: {exc}")

    def _is_builtin_asset(self, path: Path) -> bool:
        """判断这个视频是不是插件自带的默认素材（内容一致就不用进备份）。"""
        for name, _duration in BUILTIN_VIDEOS.values():
            if path.name != name:
                continue
            source = self.assets_dir / name
            try:
                return source.is_file() and source.stat().st_size == path.stat().st_size
            except OSError:
                return False
        return False

    def _backup_data(self, reason: str = "auto") -> str | None:
        """把配置和视频复制到 backups/，更新/清空槽位前调用。"""
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
                    if self._is_builtin_asset(row):
                        continue
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
        root = Path(__file__).resolve().parent
        target = self.public_dir / EXPORT_NAME
        tmp = target.with_suffix(".tmp.zip")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(root)
                    if any(part in {"__pycache__", "data", "backups", "videos", "uploads"} for part in rel.parts):
                        continue
                    if path.name != "LICENSE" and path.suffix not in {".py", ".yaml", ".yml", ".json", ".md", ".html", ".js", ".css", ".fishtheme"}:
                        continue
                    archive.write(path, f"{PLUGIN_NAME}/{rel.as_posix()}")
            tmp.replace(target)
            self.export_file = target
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋导出插件失败: {exc}")

    def _export_url(self) -> str:
        base = self._public_url()
        return base.rsplit("/", 1)[0] + "/" + EXPORT_NAME

    def _install_builtin_videos(self) -> None:
        """把插件自带视频复制到数据目录；首次安装时用它填满空槽位。"""
        if not self.assets_dir.is_dir():
            return
        first_run = not int(self.settings.get("defaults_applied") or 0)
        changed = False
        for slot, (name, duration) in BUILTIN_VIDEOS.items():
            source = self.assets_dir / name
            if not source.is_file():
                continue
            target = self.video_dir / name
            try:
                if not target.is_file() or target.stat().st_size != source.stat().st_size:
                    shutil.copy2(source, target)
                    changed = True
            except OSError as exc:
                logger.warning(f"大肥鱼的小屋安装内置视频失败: {exc}")
                continue
            if first_run and not self.videos.get(slot):
                self.videos[slot] = f"videos/{name}"
                self.durations[slot] = duration
                changed = True
            elif (
                self.videos.get(slot) == f"videos/{name}"
                and abs(self._duration(slot) - duration) > 0.01
            ):
                # 内置素材被替换时，同步更新配置里的时长。
                self.durations[slot] = duration
                changed = True
        if first_run:
            self.settings["defaults_applied"] = 1
            changed = True
        if changed:
            self._write_config()

    async def _install_default_theme(self) -> bool:
        asset = Path(__file__).resolve().parent / "assets" / "default_theme.fishtheme"
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
        self._install_builtin_videos()
        if first_run:
            await self._install_default_theme()
            self.settings["defaults_applied"] = 1
            self._write_config()
        await self._ensure_durations()
        await self._sync_public()
        self._build_export_zip()
        logger.info(f"大肥鱼的小屋已加载，公共页面：{self._prefix()}/index.html")

    async def terminate(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    # ------------------------------------------------------------------ 状态机

    async def _set_state(
        self,
        mode: str,
        event: str,
        *,
        phone_until: float = 0,
        auto_work: bool = False,
        bubble: str | None = None,
        bubble_until: float | None = None,
        bubble_source: str = "auto",
        after_phone: str | None = None,
        idle_until: float | None = None,
        arrive_in: float | None = None,
        action_key: str | None = None,
        action_loop: bool = False,
        node_id: str | None = None,
        node_video: str | None = None,
        node_loop: bool = False,
        node_label: str | None = None,
    ) -> int:
        async with self._lock:
            self.state.update(
                {
                    "mode": mode,
                    "event": event,
                    "phone_until": float(phone_until or 0),
                    "work": mode == "computer",
                    "auto_work": bool(auto_work) if mode == "computer" else False,
                    "updated_at": time.time(),
                    "action_loop": bool(action_loop) if mode == "action" else False,
                }
            )
            if action_key is not None:
                self.state["action_key"] = str(action_key)
            elif mode != "action":
                self.state["action_key"] = ""
            if node_id is not None:
                self.state["node_id"] = str(node_id)
            elif mode != "flow":
                self.state["node_id"] = ""
            if node_video is not None:
                self.state["node_video"] = str(node_video)
            elif mode != "flow":
                self.state["node_video"] = ""
            self.state["node_loop"] = bool(node_loop) if mode == "flow" else False
            if node_label is not None:
                self.state["node_label"] = str(node_label)
            elif mode != "flow":
                self.state["node_label"] = ""
            if after_phone is not None:
                self.state["after_phone"] = str(after_phone).strip()
            if mode != "phone":
                self.state["after_phone"] = ""
            if idle_until is not None:
                self.state["idle_until"] = float(idle_until or 0)
            if mode != "computer":
                self.state["idle_until"] = 0
                self.state["task_done"] = False
            if arrive_in is not None:
                self.state["arrive_at"] = time.time() + max(0.0, float(arrive_in or 0))
            elif mode != "computer":
                self.state["arrive_at"] = 0
            if bubble is not None:
                self.state["bubble"] = str(bubble).strip()[:120]
                self.state["bubble_until"] = float(bubble_until or 0)
                self.state["bubble_source"] = (
                    str(bubble_source or "").strip()
                    if str(bubble).strip()
                    else ""
                )
            elif bubble_until is not None:
                self.state["bubble_until"] = float(bubble_until or 0)
            self.state["seq"] = int(self.state.get("seq") or 0) + 1
            self._write_state_locked()
            seq = int(self.state["seq"])
        if (
            mode == "phone"
            and phone_until > time.time()
            and self.state.get("after_phone") != "computer"
        ):
            task = asyncio.create_task(self._phone_timeout())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        if mode == "phone" and phone_until > time.time():
            # 按拿手机/玩手机/放手机的时长，依次把画布高亮推到对应节点。
            self._stage_token += 1
            token = self._stage_token
            stage_task = asyncio.create_task(self._set_stage("phone_pickup"))
            self._tasks.add(stage_task)
            stage_task.add_done_callback(self._tasks.discard)
            track_task = asyncio.create_task(self._track_phone_stages(token))
            self._tasks.add(track_task)
            track_task.add_done_callback(self._tasks.discard)
        return seq

    async def _phone_timeout(self) -> None:
        try:
            while True:
                async with self._lock:
                    if self.state.get("mode") != "phone":
                        return
                    if self.state.get("after_phone") == "computer":
                        return
                    until = float(self.state.get("phone_until") or 0)
                    if until <= 0:
                        return
                    remaining = until - time.time()
                    if remaining <= 0:
                        self.state.update(
                            {
                                "mode": "rest",
                                "event": "phone_timeout",
                                "phone_until": 0,
                                "work": False,
                                "after_phone": "",
                                "idle_until": 0,
                                "updated_at": time.time(),
                            }
                        )
                        self.state["seq"] = int(self.state.get("seq") or 0) + 1
                        self._write_state_locked()
                        return
                await asyncio.sleep(min(1.0, max(0.1, remaining)))
        except asyncio.CancelledError:
            return

    def _duration(self, slot: str) -> float:
        try:
            return max(0.0, float(self.durations.get(slot) or 0))
        except (TypeError, ValueError):
            return 0.0

    def _phone_duration(self) -> float:
        return sum(self._duration(key) for key in ("phone_pickup", "phone_loop", "phone_putdown"))

    def _work_duration(self) -> float:
        return sum(self._duration(key) for key in ("computer_go", "computer_loop", "computer_rest"))

    def _idle_seconds(self) -> float:
        try:
            return max(10.0, float(self._cfg("idle_seconds", 60) or 60))
        except (TypeError, ValueError):
            return 60.0

    def _schedule_idle(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        task = asyncio.create_task(self._idle_timeout())
        self._idle_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _schedule_work_guard(self) -> None:
        """兜底：就算模型一直不结束任务，也不能永远坐在电脑前。"""
        if self._guard_task is not None and not self._guard_task.done():
            self._guard_task.cancel()
        try:
            max_seconds = max(60.0, float(self._cfg("max_work_seconds", 1800) or 1800))
        except (TypeError, ValueError):
            max_seconds = 1800.0

        async def guard() -> None:
            try:
                await asyncio.sleep(max_seconds)
                if self.state.get("mode") == "computer" and not self.state.get("task_done"):
                    logger.info("大肥鱼的小屋连续干活超过上限，强制回休息")
                    await self.work_end(force=True)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(guard())
        self._guard_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _idle_timeout(self) -> None:
        try:
            while True:
                expired = False
                async with self._lock:
                    if self.state.get("mode") != "computer":
                        return
                    until = float(self.state.get("idle_until") or 0)
                    if until <= 0:
                        return
                    remaining = until - time.time()
                    if remaining <= 0:
                        expired = True
                if expired:
                    await self.work_end(force=True)
                    return
                await asyncio.sleep(min(1.0, max(0.1, remaining)))
        except asyncio.CancelledError:
            return

    async def _arm_idle(self, seconds: float | None = None) -> None:
        """重新开始“没任务就回休息”的倒计时（只改内存，不写文件，减少占用）。"""
        if self.state.get("mode") != "computer":
            return
        idle = self._idle_seconds() if seconds is None else max(1.0, float(seconds))
        self.state["idle_until"] = time.time() + idle
        self._schedule_idle()

    async def _pause_idle(self) -> None:
        """工具执行期间暂停倒计时，避免干到一半被赶回休息。"""
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        if self.state.get("mode") != "computer":
            return
        self.state["idle_until"] = 0

    async def _lie_after_delay(self, delay: float) -> None:
        """兜底：用户要回去躺着时，消息流程没触发也切到换衣躺下的节点。"""
        try:
            await asyncio.sleep(max(1.0, float(delay)))
            if self.state.get("mode") in {"computer", "phone", "action", "flow"}:
                if not await self.flow_goto("lie_down"):
                    await self.force_rest()
        except asyncio.CancelledError:
            return

    async def _rest_after_delay(self, delay: float) -> None:
        """兜底：用户说要休息后，如果消息流程没触发，也在这段时间后回去。"""
        try:
            await asyncio.sleep(max(1.0, float(delay)))
            if self.state.get("mode") in {"computer", "phone", "action", "flow"}:
                await self.force_rest()
        except asyncio.CancelledError:
            return

    async def set_bubble(self, text: str, ttl_seconds: int = 0) -> None:
        text = str(text or "").strip()[:120]
        until = time.time() + int(ttl_seconds) if int(ttl_seconds or 0) > 0 else 0
        async with self._lock:
            self.state["bubble"] = text
            self.state["bubble_until"] = until
            self.state["bubble_source"] = "custom" if text else ""
            self.state["updated_at"] = time.time()
            self.state["seq"] = int(self.state.get("seq") or 0) + 1
            self._write_state_locked()

    async def note_reply(self) -> None:
        if not self._cfg("trigger_on_reply", True):
            return
        if self.state.get("mode") == "flow":
            # 画布模式下由流程图决定动作，不被普通回复打断。
            return
        if self.state.get("mode") == "computer":
            await self.work_ping()
            return
        if self.state.get("mode") == "phone":
            return
        if not any(
            self.videos.get(key)
            for key in ("phone_pickup", "phone_loop", "phone_putdown")
        ):
            return
        now = time.time()
        duration = self._phone_duration()
        until = now + duration if duration > 0 else 0
        current_bubble = str(self.state.get("bubble") or "").strip()
        current_until = float(self.state.get("bubble_until") or 0)
        preserve_custom = (
            self.state.get("bubble_source") == "custom"
            and current_bubble
            and (not current_until or current_until > now)
        )
        bubble = current_bubble if preserve_custom else "回消息"
        await self._set_state(
            "phone",
            "reply",
            phone_until=until,
            bubble=bubble,
            bubble_until=(current_until if preserve_custom else now + 8),
            bubble_source="custom" if preserve_custom else "auto",
        )

    async def work_ping(self, activity: str = "") -> None:
        if self.state.get("mode") != "computer":
            return
        if activity:
            self._last_activity = str(activity).strip()
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        now = time.time()
        async with self._lock:
            # 还在干活：撤销「已结束」标记，并把回休息倒计时往后推。
            self.state["task_done"] = False
            self.state["idle_until"] = now + self._idle_seconds()
            self.state["event"] = "work_ping"
            self.state["updated_at"] = now
            if activity:
                self.state["bubble"] = str(activity).strip()[:120]
                self.state["bubble_until"] = 0
                self.state["bubble_source"] = "custom"
                self.state["seq"] = int(self.state.get("seq") or 0) + 1
            self._write_state_locked()
        self._schedule_idle()

    async def go_computer(
        self,
        *,
        umo: str = "",
        activity: str = "",
        arrival_text: str = "",
    ) -> bool:
        mode = self.state.get("mode") or "rest"
        if activity:
            self._last_activity = str(activity).strip()
        if mode == "computer":
            await self.work_ping(activity)
            return False
        if not any(
            self.videos.get(key)
            for key in ("phone_pickup", "phone_loop", "phone_putdown")
        ):
            await self.work_start(activity=activity)
            self._schedule_arrival(umo, arrival_text, activity)
            return True
        now = time.time()
        self._go_token += 1
        go_token = self._go_token
        if mode == "phone":
            until = float(self.state.get("phone_until") or 0)
            delay = max(0.8, until - now + 0.4)
        else:
            delay = max(0.8, self._phone_duration() + 0.4)
            until = now + delay
        await self._set_state(
            "phone",
            "go_computer",
            phone_until=until,
            after_phone="computer",
            bubble="回消息",
            bubble_until=now + 8,
            bubble_source="auto",
            arrive_in=delay + self._duration("computer_go"),
        )
        task = asyncio.create_task(
            self._finish_go_computer(delay, activity, umo, arrival_text, go_token)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _finish_go_computer(
        self, delay: float, activity: str, umo: str, arrival_text: str, go_token: int
    ) -> None:
        try:
            await asyncio.sleep(delay)
            if go_token != self._go_token:
                return
            if (
                self.state.get("mode") != "phone"
                or self.state.get("after_phone") != "computer"
            ):
                return
            await self._enter_computer(activity)
            self._schedule_arrival(umo, arrival_text, activity)
        except asyncio.CancelledError:
            return

    async def _enter_computer(self, activity: str = "") -> None:
        activity = str(activity or self._last_activity or "").strip()
        done = bool(self.state.get("task_done"))
        await self._set_state(
            "computer",
            "work_start",
            after_phone="",
            idle_until=time.time() + self._idle_seconds(),
            bubble=("忙完啦，在电脑前歇一会儿" if done else (activity or "在电脑前干活")),
            bubble_until=(time.time() + 8 if done else 0),
            bubble_source="auto" if done or not activity else "custom",
            arrive_in=self._duration("computer_go"),
        )
        self._schedule_idle()
        self._schedule_work_guard()
        self._start_computer_stage()

    async def wait_until_arrived(self, timeout: float = 45.0) -> bool:
        """等到桌宠真正走到电脑旁边（按视频时长估算），超时返回 False。"""
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            if self.state.get("mode") == "computer":
                arrive_at = float(self.state.get("arrive_at") or 0)
                if arrive_at <= 0 or time.time() >= arrive_at:
                    return True
            await asyncio.sleep(0.2)
        logger.warning("大肥鱼的小屋等待桌宠到电脑超时")
        return False

    def _schedule_arrival(self, umo: str, arrival_text: str, activity: str) -> None:
        if not umo:
            return
        task = asyncio.create_task(self._send_arrival(umo, arrival_text, activity))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_arrival(self, umo: str, arrival_text: str, activity: str) -> None:
        try:
            await asyncio.sleep(self._duration("computer_go") + 0.8)
            if self.state.get("mode") != "computer":
                return
            activity = str(activity or self._last_activity or "").strip()
            text = str(arrival_text or "").strip()
            if not text:
                if self.state.get("task_done"):
                    text = "我到电脑旁边了，忙完啦。"
                else:
                    text = "我到电脑旁边了，开始干活。"
                    if activity:
                        text = f"我到电脑旁边了，{activity}。"
            await self.context.send_message(umo, MessageChain().message(text))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋电脑到达消息失败: {exc}")

    async def work_start(self, *, auto: bool = False, activity: str = "") -> bool:
        self._last_activity = str(activity or "").strip()
        if self.state.get("mode") == "computer":
            await self.work_ping(activity)
            return False
        self.state["task_done"] = False
        await self._set_state(
            "computer",
            "work_start",
            auto_work=auto,
            idle_until=time.time() + self._idle_seconds(),
            bubble=activity or "在电脑前干活",
            bubble_until=0,
            bubble_source="custom" if activity else "auto",
            arrive_in=self._duration("computer_go"),
        )
        self._schedule_idle()
        self._schedule_work_guard()
        self._start_computer_stage()
        return True

    async def work_end(self, *, force: bool = True) -> None:
        mode = self.state.get("mode")
        if mode not in {"computer", "phone"}:
            return
        if mode == "phone" and self.state.get("after_phone") == "computer" and not force:
            async with self._lock:
                self.state["task_done"] = True
                self.state["bubble"] = "忙完啦，快到电脑了"
                self.state["bubble_until"] = time.time() + 8
                self.state["bubble_source"] = "auto"
                self.state["updated_at"] = time.time()
                self.state["seq"] = int(self.state.get("seq") or 0) + 1
                self._write_state_locked()
            return
        if mode == "computer" and not force:
            async with self._lock:
                self.state["task_done"] = True
                self.state["event"] = "work_done"
                self.state["idle_until"] = time.time() + self._idle_seconds()
                self.state["bubble"] = "忙完啦，在电脑前歇一会儿"
                self.state["bubble_until"] = time.time() + 8
                self.state["bubble_source"] = "auto"
                self.state["updated_at"] = time.time()
                self.state["seq"] = int(self.state.get("seq") or 0) + 1
                self._write_state_locked()
            self._schedule_idle()
            return
        self._last_activity = ""
        await self._set_state(
            "rest",
            "work_end",
            bubble="忙完啦，回窝里歇着",
            bubble_until=time.time() + 8,
            idle_until=0,
        )

    async def force_rest(self) -> None:
        self._last_activity = ""
        await self._set_state(
            "rest",
            "rest",
            bubble="回窝里歇着",
            bubble_until=time.time() + 8,
            idle_until=0,
        )

    # ------------------------------------------------------------------ 动作触发

    async def play_action(
        self, key: str, *, loop: bool = False, text: str = "", umo: str = ""
    ) -> bool:
        key = str(key or "").strip()
        if key not in self._action_keys() or not self.videos.get(key):
            return False
        item = self._action_def(key)
        loop = bool(loop or item.get("loop"))
        await self._set_state(
            "action",
            "play_action",
            bubble=text or str(item.get("label") or key),
            bubble_until=0,
            action_key=key,
            action_loop=loop,
        )
        if not loop:
            # URL 动作可能没有时长，至少给它 1.5 秒，别一闪而过。
            task = asyncio.create_task(
                self._end_action_after(key, max(1.5, self._duration(key) + 0.3))
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return True

    async def _end_action_after(self, key: str, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.2, float(delay)))
            if (
                self.state.get("mode") == "action"
                and self.state.get("action_key") == key
            ):
                await self.force_rest()
        except asyncio.CancelledError:
            return

    async def add_action(self, key: str, label: str = "", hint: str = "", loop: bool = False) -> bool:
        key = str(key or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key):
            return False
        if key in self._action_keys():
            return False
        self.actions.append(
            {
                "key": key,
                "label": str(label or key)[:40],
                "hint": str(hint or "")[:120],
                "loop": bool(loop),
            }
        )
        self._sync_action_slots()
        self._write_config()
        return True

    async def remove_action(self, key: str) -> bool:
        key = str(key or "").strip()
        if key in SLOT_KEYS:
            return False
        if key not in self._action_keys():
            return False
        self.actions = [item for item in self.actions if item.get("key") != key]
        self.videos.pop(key, None)
        self.audio.pop(key, None)
        self.durations.pop(key, None)
        self._write_config()
        return True

    # ------------------------------------------------------------------ 素材库

    def _normalize_library(self, raw: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        ids: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or "").strip()
                url = str(item.get("video") or "").strip()
                if not mid or not url or mid in ids:
                    continue
                ids.add(mid)
                items.append(
                    {
                        "id": mid,
                        "name": str(item.get("name") or Path(url).stem)[:40],
                        "video": url,
                        "duration": max(0.0, float(item.get("duration") or 0)),
                    }
                )
        return items

    def _library_item(self, media_id: str) -> dict[str, Any] | None:
        for item in self.library:
            if item.get("id") == media_id:
                return item
        return None

    def _node_video(self, node: dict[str, Any]) -> str:
        item = self._library_item(str(node.get("media_id") or ""))
        if item:
            return str(item.get("video") or "")
        return str(node.get("video") or "")

    def _ensure_library(self) -> None:
        """把旧数据里的视频收进素材库，并给画布节点补 media_id。"""
        by_url = {item["video"]: item for item in self.library if item.get("video")}
        used_ids = {item["id"] for item in self.library}

        def add(url: str, name: str, duration: float = 0) -> dict[str, Any] | None:
            url = str(url or "").strip()
            if not url:
                return None
            if url in by_url:
                item = by_url[url]
                if duration and not item.get("duration"):
                    item["duration"] = float(duration)
                return item
            mid = f"m_{uuid.uuid4().hex[:8]}"
            while mid in used_ids:
                mid = f"m_{uuid.uuid4().hex[:8]}"
            used_ids.add(mid)
            item = {
                "id": mid,
                "name": (name or Path(url).stem)[:40],
                "video": url,
                "duration": max(0.0, float(duration or 0)),
            }
            self.library.append(item)
            by_url[url] = item
            return item

        for key, url in self.videos.items():
            label = self._action_def(key).get("label") or key
            add(url, str(label), float(self.durations.get(key) or 0))
        for node in self.flow.get("nodes", []):
            item = add(
                str(node.get("video") or ""),
                str(node.get("label") or ""),
                float(node.get("duration") or 0),
            )
            if item:
                node["media_id"] = item["id"]

    # ------------------------------------------------------------------ 流程图

    def _normalize_flow(self, raw: Any) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        ids: set[str] = set()
        if isinstance(raw, dict):
            for item in raw.get("nodes") or []:
                if not isinstance(item, dict):
                    continue
                nid = str(item.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", nid) or nid in ids:
                    continue
                ids.add(nid)
                nodes.append(
                    {
                        "id": nid,
                        "label": str(item.get("label") or nid)[:40],
                        "video": str(item.get("video") or ""),
                        "loop": bool(item.get("loop")),
                        "role": str(item.get("role") or "action")[:20],
                        "prompt": str(item.get("prompt") or "")[:300],
                        "note": str(item.get("note") or "")[:80],
                        "duration": max(0.0, float(item.get("duration") or 0)),
                        "media_id": str(item.get("media_id") or ""),
                        "x": float(item.get("x") or 100),
                        "y": float(item.get("y") or 100),
                    }
                )
        edges: list[dict[str, Any]] = []
        used_edges: set[str] = set()
        if isinstance(raw, dict):
            for item in raw.get("edges") or []:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("from") or "")
                dst = str(item.get("to") or "")
                if src not in ids or dst not in ids:
                    continue
                eid = str(item.get("id") or f"{src}->{dst}")
                if eid in used_edges:
                    eid = f"{eid}-{len(used_edges)}"
                used_edges.add(eid)
                edges.append(
                    {
                        "id": eid,
                        "from": src,
                        "to": dst,
                        "label": str(item.get("label") or "")[:120],
                    }
                )
        return {"nodes": nodes, "edges": edges}

    def _seed_flow(self) -> dict[str, Any]:
        videos = self.videos

        def node(nid, label, role, loop, x, y):
            return {
                "id": nid,
                "label": label,
                "video": videos.get(nid, ""),
                "loop": loop,
                "role": role,
                "prompt": "",
                "note": "",
                "duration": float(self.durations.get(nid) or 0),
                "x": x,
                "y": y,
            }

        nodes = [
            node("rest", "休息", "wait", True, 80, 220),
            node("phone_pickup", "拿起手机", "action", False, 330, 80),
            node("phone_loop", "回消息", "phone", True, 580, 80),
            node("phone_putdown", "放下手机", "transition", False, 830, 80),
            node("computer_go", "去电脑", "transition", False, 330, 380),
            node("computer_loop", "玩电脑", "both", True, 580, 380),
            node("computer_rest", "电脑回休息", "transition", False, 830, 380),
        ]
        edges = [
            {"id": "e_rest_phone", "from": "rest", "to": "phone_pickup", "label": "有人找、要回消息"},
            {"id": "e_rest_comp", "from": "rest", "to": "computer_go", "label": "要干活、查资料、刷抖音"},
            {"id": "e_pick_loop", "from": "phone_pickup", "to": "phone_loop", "label": ""},
            {"id": "e_loop_put", "from": "phone_loop", "to": "phone_putdown", "label": ""},
            {"id": "e_put_rest", "from": "phone_putdown", "to": "rest", "label": ""},
            {"id": "e_put_computer", "from": "phone_putdown", "to": "computer_go", "label": "去电脑干活"},
            {"id": "e_go_loop", "from": "computer_go", "to": "computer_loop", "label": ""},
            {"id": "e_loop_rest", "from": "computer_loop", "to": "computer_rest", "label": "活干完了"},
            {"id": "e_crest_rest", "from": "computer_rest", "to": "rest", "label": ""},
        ]
        return {"nodes": nodes, "edges": edges}

    def _flow_node(self, node_id: str) -> dict[str, Any] | None:
        for item in self.flow.get("nodes") or []:
            if item.get("id") == node_id:
                return item
        return None

    def _flow_outgoing(self, node_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.flow.get("edges") or []
            if item.get("from") == node_id
        ]

    def _node_for_action(self, key: str) -> str:
        node = self._flow_node(key)
        if node:
            return str(node.get("id") or "")
        url = str(self.videos.get(key) or "")
        if url:
            for item in self.flow.get("nodes") or []:
                if self._node_video(item) == url:
                    return str(item.get("id") or "")
        role = {
            "rest": "wait",
            "phone_pickup": "phone",
            "phone_loop": "phone",
            "phone_putdown": "phone",
            "computer_loop": "work",
            "computer_go": "transition",
            "computer_rest": "transition",
        }.get(key, "")
        if role:
            for item in self.flow.get("nodes") or []:
                if item.get("role") == role:
                    return str(item.get("id") or "")
        return ""

    async def _set_stage(self, key: str) -> None:
        """只更新画布高亮节点，不改变播放状态、不推进 seq。"""
        if self.state.get("mode") not in {"phone", "computer"}:
            return
        nid = self._node_for_action(key)
        if not nid:
            return
        node = self._flow_node(nid) or {}
        async with self._lock:
            self.state["node_id"] = nid
            self.state["node_label"] = str(node.get("label") or nid)
            self.state["node_video"] = ""
            self.state["node_loop"] = False
            self._write_state_locked()

    async def _track_phone_stages(self, token: int) -> None:
        try:
            await asyncio.sleep(max(0.15, self._duration("phone_pickup")))
            if token != self._stage_token or self.state.get("mode") != "phone":
                return
            await self._set_stage("phone_loop")
            await asyncio.sleep(max(0.15, self._duration("phone_loop")))
            if token != self._stage_token or self.state.get("mode") != "phone":
                return
            await self._set_stage("phone_putdown")
        except asyncio.CancelledError:
            return

    async def _track_computer_stages(self, token: int) -> None:
        try:
            await asyncio.sleep(max(0.15, self._duration("computer_go")))
            if token != self._stage_token or self.state.get("mode") != "computer":
                return
            await self._set_stage("computer_loop")
        except asyncio.CancelledError:
            return

    def _start_computer_stage(self) -> None:
        self._stage_token += 1
        token = self._stage_token
        go_task = asyncio.create_task(self._set_stage("computer_go"))
        self._tasks.add(go_task)
        go_task.add_done_callback(self._tasks.discard)
        track_task = asyncio.create_task(self._track_computer_stages(token))
        self._tasks.add(track_task)
        track_task.add_done_callback(self._tasks.discard)

    async def flow_goto(self, node_id: str, *, text: str = "", umo: str = "") -> bool:
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            return False
        nid = node["id"]
        video = self._node_video(node)
        await self._set_state(
            "flow",
            "flow_node",
            bubble=text or str(node.get("label") or nid),
            bubble_until=0,
            node_id=nid,
            node_video=video,
            node_loop=bool(node.get("loop")),
            node_label=str(node.get("label") or nid),
        )
        if not node.get("loop"):
            duration = float(node.get("duration") or 0)
            if duration <= 0:
                item = self._library_item(str(node.get("media_id") or ""))
                duration = float((item or {}).get("duration") or 0)
            if duration <= 0:
                duration = 5.0
            task = asyncio.create_task(self._flow_advance_after(nid, duration + 0.25))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return True

    async def _flow_advance_after(self, node_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.5, delay))
            if self.state.get("mode") != "flow" or self.state.get("node_id") != node_id:
                return
            outgoing = self._flow_outgoing(node_id)
            if len(outgoing) == 1:
                await self.flow_goto(str(outgoing[0].get("to") or ""))
            # 多条出边：停在当前节点，等模型/用户选择。
        except asyncio.CancelledError:
            return


    # ------------------------------------------------------------------ AstrBot 事件

    def _looks_like_task(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value or value.startswith(("/小屋", "/桌宠")):
            return False
        words = (
            "查", "搜", "天气", "资料", "打开", "浏览", "下载", "写", "整理",
            "文件", "抖音", "视频", "电脑", "网页", "新闻", "翻译", "计算",
            "代码", "邮件", "日程", "提醒", "记账", "看看", "找一下", "找一个",
        )
        return any(word in value for word in words)

    def _auto_activity(self, text: str) -> str:
        text = str(text or "")
        if "天气" in text:
            return "帮主人查天气"
        if "抖音" in text or "视频" in text:
            return "我去逛一下抖音"
        if "文件" in text or "整理" in text:
            return "帮主人整理文件"
        if "资料" in text or "搜索" in text or "查" in text:
            return "帮主人查资料"
        return "帮主人处理点事"

    def _is_task_tool(self, names: list[str]) -> bool:
        skip = {
            "house_activity",
            "house_work_start",
            "house_work_end",
            "house_work_ping",
        }
        keywords = (
            "search",
            "browser",
            "fetch",
            "web",
            "weather",
            "http",
            "download",
            "shell",
            "python",
            "file",
            "read",
            "write",
            "code",
            "image",
            "video",
            "music",
            "calendar",
            "mail",
            "todo",
        )
        for name in names or []:
            key = str(name or "").lower()
            if not key or key in skip:
                continue
            if any(word in key for word in keywords):
                return True
        return False

    def _looks_like_lie_down(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        words = (
            "躺下",
            "躺着",
            "躺床",
            "回床",
            "上床",
            "睡觉",
            "睡吧",
            "困了",
            "累了",
            "去睡",
        )
        return any(word in value for word in words)

    def _looks_like_rest(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        words = (
            "休息",
            "回去",
            "回窝",
            "下班",
            "收工",
            "别干",
            "不干",
            "停下",
            "歇着",
            "歇一会",
            "歇一会儿",
        )
        return any(word in value for word in words)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: LLMResponse) -> None:
        if not self._can_feedback(event):
            return
        """模型调用查资料/浏览器等工具时，自动去电脑，不依赖模型记得调用工具。"""
        try:
            if response.is_chunk:
                return
            if not event.is_private_chat() or not self.public_prefix_enabled():
                return
            if not self._cfg("auto_reply", True):
                return
            names = list(response.tools_call_name or [])
            if names and self.state.get("mode") == "computer":
                # 工具执行期间先停表，等这一轮转完再重新计时。
                await self._pause_idle()
            if not names or "house_work_start" in names:
                return
            if not self._is_task_tool(names):
                return
            if self.state.get("mode") == "flow":
                return
            activity = self._auto_activity(event.message_str)
            await self.go_computer(
                umo=event.unified_msg_origin,
                activity=activity,
            )
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋自动去电脑失败: {exc}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self._can_feedback(event):
            return
        """让机器人知道桌宠工具，并在做电脑任务时主动写活动气泡。"""
        try:
            if not event.is_private_chat() or not self.public_prefix_enabled():
                return
            mode = self.state.get("mode") or "rest"
            heading = (
                mode == "phone" and self.state.get("after_phone") == "computer"
            )
            arrive_at = float(self.state.get("arrive_at") or 0)
            at_computer = (
                mode == "computer" and (arrive_at <= 0 or time.time() >= arrive_at)
            )
            resting = False
            if (
                mode in {"rest", "computer", "phone", "action", "flow"}
                and self._flow_node("lie_down") is not None
                and self._looks_like_lie_down(event.message_str)
            ):
                # 用户说累了/回去躺着/睡觉：回复完切到“换衣躺下”节点。
                event.set_extra("_fish_lie_now", True)
                task = asyncio.create_task(self._lie_after_delay(20.0))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                resting = True
            elif mode in {"computer", "phone", "action", "flow"} and self._looks_like_rest(
                event.message_str
            ):
                # 用户明确说“回去休息”：先在原地回复，这条消息发完再回休息。
                event.set_extra("_fish_rest_now", True)
                task = asyncio.create_task(self._rest_after_delay(20.0))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                resting = True
            if (
                not resting
                and mode != "flow"
                and self._cfg("auto_reply", True)
                and self._looks_like_task(event.message_str)
                and not at_computer
            ):
                if not heading and mode != "computer":
                    await self.go_computer(
                        umo=event.unified_msg_origin,
                        activity=self._auto_activity(event.message_str),
                    )
                # 走到电脑之前先卡住：后面的 LLM 和工具调用都要等桌宠到位。
                await self.wait_until_arrived()
                await self.work_ping()
            if self.state.get("mode") == "computer":
                # 用户来了新消息，说明还在互动，倒计时重新开始。
                await self._arm_idle()
            prompt = (
                "\n【大肥鱼桌宠】你在私聊里有一只桌面宠物。"
                "当你要查资料、写东西、整理文件、逛网页、看视频等需要坐在电脑前的任务时，"
                "先调用 house_work_start(activity=一句自然的话)。"
                "这个工具会等桌宠真正走到电脑旁边才返回，返回后再执行实际操作；"
                "没到电脑之前不要先打开浏览器、抖音或别的应用。"
                "任务结束调用 house_work_end。"
                "要播放面板里配置的自定义动作时调用 house_play_action(action=动作 key)。"
                "长任务每 30-50 秒调用 house_work_ping 续期，超过一分钟没任务会自动回休息。"
                "只闲聊时不用调用。活动气泡要像你本人在说话，例如「帮主人查资料」「我去逛一下抖音」。"
            )
            current = str(getattr(req, "system_prompt", "") or "")
            if "house_work_start" not in current:
                current += prompt
            flow_nodes = self.flow.get("nodes") or []
            if flow_nodes:
                cur_id = (
                    str(self.state.get("node_id") or "")
                    if self.state.get("mode") == "flow"
                    else ""
                )
                node_brief = "；".join(
                    f"{item.get('id')}={item.get('label')}"
                    + ("[循环]" if item.get("loop") else "")
                    for item in flow_nodes[:20]
                )
                flow_hint = f"\n【流程图】节点：{node_brief}。"
                if cur_id:
                    options = []
                    for edge in self._flow_outgoing(cur_id):
                        target = self._flow_node(str(edge.get("to") or ""))
                        if target is None:
                            continue
                        reason = target.get("prompt") or edge.get("label") or ""
                        options.append(
                            f"{target.get('id')}={target.get('label')}"
                            + (f"（{reason}）" if reason else "")
                        )
                    flow_hint += f"当前在 {cur_id}。"
                    if options:
                        flow_hint += "下一步可选：" + "；".join(options) + "。"
                if self._flow_node("lie_down"):
                    flow_hint += (
                        "用户说累了、困了、回去躺着/睡觉时，"
                        '调用 house_flow_goto(node_id="lie_down") 先换衣服再躺下。'
                    )
                flow_hint += "需要切换动作节点时调用 house_flow_goto(node_id=...)。"
                if "【流程图】" not in current:
                    current += flow_hint
            req.system_prompt = current
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋注入提示失败: {exc}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not self._can_feedback(event):
            return
        """回复先等桌宠拿到手机，再真正发出去。"""
        try:
            if not self.public_prefix_enabled():
                return
            if event.get_extra("_fish_rest_now") or event.get_extra("_fish_lie_now"):
                # 用户要求回去休息/躺下：这条回复直接发，不再等手机/加延迟。
                return
            if not self._cfg("auto_reply", True):
                return
            if not self._cfg("trigger_on_reply", True):
                return
            if event.get_extra("_fish_reply_started"):
                return
            text = str(event.message_str or "")
            if text.startswith("/小屋") or text.startswith("/桌宠"):
                return
            result = event.get_result()
            if result is None or not result.is_llm_result():
                return
            chain = getattr(result, "chain", None)
            if isinstance(chain, (list, tuple)) and chain and not any(
                isinstance(part, Comp.Plain) for part in chain
            ):
                return
            mode = self.state.get("mode") or "rest"
            state_event = str(self.state.get("event") or "")
            updated_at = float(self.state.get("updated_at") or 0)
            now = time.time()
            if mode == "flow" or mode == "action":
                # 画布/自定义动作模式下原地回复，不插入拿手机动画。
                event.set_extra("_fish_reply_started", True)
                return
            if mode == "computer":
                event.set_extra("_fish_reply_started", True)
                if state_event == "work_start":
                    remaining = self._duration("computer_go") - (now - updated_at)
                    if remaining > 0:
                        await asyncio.sleep(min(15.0, remaining + 0.8))
                # 这一轮回复结束，从这里开始算“一分钟没任务就回休息”。
                await self._arm_idle()
                return
            if mode == "phone":
                event.set_extra("_fish_reply_started", True)
                if state_event in {"reply", "go_computer"}:
                    remaining = self._duration("phone_pickup") - (now - updated_at)
                    if remaining > 0:
                        await asyncio.sleep(min(15.0, remaining + 0.8))
                return
            await self.note_reply()
            event.set_extra("_fish_reply_started", True)
            if mode == "rest":
                delay = self._duration("phone_pickup")
                if delay > 0:
                    await asyncio.sleep(min(15.0, delay + 0.8))
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋延迟回复失败: {exc}")

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent) -> None:
        if not self._can_feedback(event):
            return
        """可选的自动钩子：Bot 发消息时播手机动画。

        干活状态不再从消息内容猜测，统一由 /event 接口或面板触发。
        """
        try:
            if not self.public_prefix_enabled():
                return
            if event.get_extra("_fish_lie_now"):
                # 回复已经发出，切到换衣躺下节点。
                if not await self.flow_goto("lie_down"):
                    await self.force_rest()
                return
            if event.get_extra("_fish_rest_now"):
                # 回复已经发出，现在再回休息（不会闪出拿手机动画）。
                await self.force_rest()
                return
            # 不管这一条是不是自动动画消息，只要在电脑前，就从这里重新计时。
            if self.state.get("mode") == "computer":
                await self._arm_idle()
            if not self._cfg("auto_reply", True):
                return
            if event.get_extra("_fish_reply_started"):
                return
            text = str(event.message_str or "")
            if text.startswith("/小屋") or text.startswith("/桌宠"):
                return
            await self.note_reply()
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋处理消息状态失败: {exc}")

    @filter.llm_tool(name="house_activity")
    async def tool_activity(self, event: AstrMessageEvent, activity: str, ttl_seconds: int = 0) -> str:
        """设置桌宠底部的活动气泡。做任务时用一句自然的话告诉用户你在干什么，例如「帮主人查资料」「我去逛一下抖音」「在整理今天的账单」。不要每轮都说，只在开始做某件事时更新。

        Args:
            activity(string): 展示在桌宠底部气泡里的一句话，尽量 20 字以内
            ttl_seconds(number): 多少秒后自动消失；0 表示一直保留到下次更新
        """
        if not event.is_private_chat():
            return "活动气泡只在私聊中设置。"
        text = str(activity or "").strip()
        if not text:
            return "气泡内容不能为空。"
        self._last_activity = text
        if self.state.get("mode") == "computer":
            await self.work_ping(text)
        else:
            await self.set_bubble(text, int(ttl_seconds or 0))
        return f"气泡已更新：{text}"

    @filter.llm_tool(name="house_work_start")
    async def tool_work_start(self, event: AstrMessageEvent, activity: str = "") -> str:
        """开始一个需要坐在电脑前完成的任务，例如查资料、整理文件、写报告、逛网页。会等桌宠走到电脑旁边后才返回，返回后才可以开始实际操作。"""
        if not event.is_private_chat():
            return "电脑动画只在私聊中启用。"
        await self.go_computer(
            umo=event.unified_msg_origin,
            activity=activity,
        )
        await self.wait_until_arrived()
        await self.work_ping()
        if self.state.get("mode") == "computer":
            return "已经到电脑旁边了，现在可以开始操作。"
        return "还在往电脑那边走，稍等一下再操作。"

    @filter.llm_tool(name="house_work_end")
    async def tool_work_end(self, event: AstrMessageEvent) -> str:
        """电脑前的任务结束，切回休息动画。"""
        if not event.is_private_chat():
            return "电脑动画只在私聊中启用。"
        await self.work_end(force=False)
        return "任务结束；桌宠会到电脑后再歇一会儿。"

    @filter.llm_tool(name="house_work_ping")
    async def tool_work_ping(self, event: AstrMessageEvent, activity: str = "") -> str:
        """长任务进行中续期，告诉桌宠还在干活。超过一分钟没有心跳会自动回休息。

        Args:
            activity(string): 可选，同时更新底部气泡
        """
        if not event.is_private_chat():
            return "电脑动画只在私聊中启用。"
        await self.work_ping(activity)
        return "已续期，桌宠知道你还在干活。"

    @filter.llm_tool(name="house_play_action")
    async def tool_play_action(self, event: AstrMessageEvent, action: str, loop: bool = False) -> str:
        """播放一个自定义动作（面板里配置的动作），播放完会自动回休息。

        Args:
            action(string): 动作 key，例如「eat」「dance」
            loop(boolean): 是否循环播放，默认 false
        """
        if not event.is_private_chat():
            return "动作只在私聊中启用。"
        item = next((x for x in self.actions if x.get("key") == action), None)
        if item is None or not self.videos.get(action):
            usable = "、".join(
                item["key"] for item in self.actions if self.videos.get(item["key"])
            )
            return f"没有这个动作或还没配视频。可用动作：{usable}"
        await self.play_action(action, loop=loop, text=str(item.get("label") or action))
        return f"开始播放「{item.get('label') or action}」。"

    @filter.llm_tool(name="house_flow_goto")
    async def tool_flow_goto(self, event: AstrMessageEvent, node_id: str) -> str:
        """按流程图切换桌宠当前动作。node_id 必须是流程图里存在的节点 id。

        Args:
            node_id(string): 要切换到的节点 id，例如「rest」「computer_loop」
        """
        if not event.is_private_chat():
            return "流程图动作只在私聊中启用。"
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            ids = "、".join(str(item.get("id")) for item in self.flow.get("nodes", []))
            return f"没有这个节点。可用节点：{ids}"
        await self.flow_goto(node["id"], umo=event.unified_msg_origin)
        return f"已切到「{node.get('label') or node['id']}」。"

    @filter.command("小屋", alias={"桌宠"})
    async def cmd_house(self, event: AstrMessageEvent) -> None:
        parts = str(event.message_str or "").split(maxsplit=2)
        action = parts[1].strip() if len(parts) > 1 else ""
        extra = parts[2].strip() if len(parts) > 2 else ""
        event.stop_event()
        if action in {"干活", "工作", "去电脑", "上班"}:
            before = self.state.get("mode") or "rest"
            await self.go_computer(
                umo=event.unified_msg_origin,
                activity=extra,
            )
            if before == "rest":
                await asyncio.sleep(min(15.0, self._duration("phone_pickup") + 0.8))
            message = (
                f"好，我去电脑那边忙一会儿。{extra}"
                if extra
                else "好，我去电脑那边忙一会儿。"
            )
        elif action in {"休息", "下班", "干完", "回来"}:
            await self.work_end()
            message = "忙完啦，回窝里歇着。"
        elif action in {"回消息", "拿手机"}:
            await self.note_reply()
            message = "我去看一眼消息。"
        elif action in {"说", "气泡", "activity"}:
            if not extra:
                message = "用法：/小屋 说 帮主人查资料"
            else:
                await self.set_bubble(extra, 0)
                message = f"气泡已更新：{extra}"
        else:
            mode = self.state.get("mode", "rest")
            label = {"rest": "休息", "phone": "手机", "computer": "电脑"}.get(mode, mode)
            message = (
                f"现在在{label}状态。\n"
                "发「/小屋 干活」「/小屋 休息」可以手动切换；公共页面在插件面板里复制。"
            )
        yield event.plain_result(message)

    # ------------------------------------------------------------------ Web API

    @staticmethod
    def _ok(**data: Any):
        return json_response({"status": "ok", **data})

    def _register_web_apis(self) -> None:
        base = f"/{PLUGIN_NAME}"
        self.context.register_web_api(
            f"{base}/overview", self.api_overview, ["GET"], "大肥鱼的小屋概览"
        )
        self.context.register_web_api(
            f"{base}/state", self.api_state, ["GET"], "桌宠当前状态"
        )
        self.context.register_web_api(
            f"{base}/event", self.api_event, ["POST"], "触发桌宠动作"
        )
        self.context.register_web_api(
            f"{base}/settings", self.api_settings, ["POST"], "保存桌宠设置"
        )
        self.context.register_web_api(
            f"{base}/slot", self.api_slot, ["POST"], "设置或清除视频槽位"
        )
        self.context.register_web_api(
            f"{base}/upload/start", self.api_upload_start, ["POST"], "开始上传视频"
        )
        self.context.register_web_api(
            f"{base}/upload/chunk", self.api_upload_chunk, ["POST"], "上传视频分片"
        )
        self.context.register_web_api(
            f"{base}/upload/finish", self.api_upload_finish, ["POST"], "完成视频上传"
        )
        self.context.register_web_api(
            f"{base}/sync", self.api_sync, ["POST"], "重新同步公共页面"
        )
        self.context.register_web_api(
            f"{base}/backups", self.api_backups, ["GET"], "列出备份"
        )
        self.context.register_web_api(
            f"{base}/restore", self.api_restore, ["POST"], "恢复备份"
        )
        self.context.register_web_api(
            f"{base}/action", self.api_action, ["POST"], "动作管理"
        )
        self.context.register_web_api(
            f"{base}/flow", self.api_flow, ["POST"], "流程图保存/生成"
        )
        self.context.register_web_api(
            f"{base}/library", self.api_library, ["POST"], "素材库管理"
        )
        self.context.register_web_api(
            f"{base}/theme", self.api_theme, ["POST"], "主题导出/导入/应用"
        )
        self.context.register_web_api(
            f"{base}/theme/project", self.api_theme_project, ["POST"], "主题项目"
        )
        self.context.register_web_api(
            f"{base}/theme/market", self.api_theme_market, ["POST"], "主题市场"
        )

    def _overview(self) -> dict:
        slots = []
        for item in self.actions:
            key = str(item.get("key") or "")
            if not key:
                continue
            label = str(item.get("label") or key)
            hint = str(item.get("hint") or "")
            url = self.videos.get(key, "")
            if url.startswith(("http://", "https://", "/")):
                preview_url = url
            elif url:
                preview_url = f"/{self._prefix()}/{url.lstrip('/')}"
            else:
                preview_url = ""
            slots.append(
                {
                    "key": key,
                    "label": label,
                    "hint": hint,
                    "loop": bool(item.get("loop")),
                    "url": url,
                    "preview_url": preview_url,
                    "filename": Path(url).name if url and not url.startswith(("http://", "https://", "/")) else url,
                    "duration": round(self._duration(key), 2),
                    "audio": bool(self.audio.get(key, True)),
                }
            )
        return {
            "version": VERSION,
            "state": dict(self.state),
            "slots": slots,
            "actions": [dict(item) for item in self.actions],
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
            "public_ready": bool(self.public_dir and (self.public_dir / "index.html").is_file()),
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
        elif action in {"reply", "回消息", "phone"}:
            await self.note_reply()
        elif action in {"go_computer", "去电脑", "拿手机去电脑"}:
            await self.go_computer(
                umo=str(payload.get("umo") or ""),
                activity=str(payload.get("activity") or payload.get("text") or ""),
                arrival_text=str(payload.get("arrival_text") or ""),
            )
        elif action in {"work_ping", "续期", "心跳"}:
            await self.work_ping(str(payload.get("activity") or ""))
        elif action in {"work_start", "干活", "computer"}:
            await self.work_start(activity=str(payload.get("activity") or ""))
        elif action in {"work_end", "干完", "休息", "rest"}:
            if action in {"休息", "rest"}:
                await self.force_rest()
            else:
                await self.work_end()
        elif action in {"reset", "初始化"}:
            await self.force_rest()
        elif action in {"play_action", "动作"}:
            key = str(payload.get("key") or payload.get("name") or "")
            loop = bool(payload.get("loop"))
            if not await self.play_action(key, loop=loop):
                return error_response("没有这个动作或还没配视频")
        elif action in {"flow_goto", "节点"}:
            node_id = str(payload.get("node_id") or payload.get("name") or "")
            if not await self.flow_goto(node_id):
                return error_response("节点不存在")
        else:
            return error_response("未知动作")
        return self._ok(state=dict(self.state))

    async def api_action(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        key = str(payload.get("key") or "").strip()
        if action == "add":
            ok = await self.add_action(
                key,
                str(payload.get("label") or ""),
                str(payload.get("hint") or ""),
                bool(payload.get("loop")),
            )
            if not ok:
                return error_response("动作 key 无效或已存在（只能用字母数字 _ . -）")
        elif action == "remove":
            if not await self.remove_action(key):
                return error_response("不能删除基础动作或该动作不存在")
        else:
            return error_response("未知操作")
        await self._sync_public()
        return self._ok(**self._overview())

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
                node.get("id")
                for node in self.flow.get("nodes", [])
                if str(node.get("media_id") or "") == media_id
            ]
            if used_by_nodes:
                return error_response("这个素材还被画布节点使用：" + "、".join(used_by_nodes[:5]))
            url = str(item.get("video") or "")
            used_by_slots = any(
                url and url == value for value in self.videos.values()
            )
            if used_by_slots:
                return error_response("这个素材还被动作槽位使用，不能删除")
            self.library = [x for x in self.library if x.get("id") != media_id]
            self._remove_local_video(url)
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
            self._ensure_task_graph(self.flow)
            self._ensure_wait_edges()
            self._ensure_library()
            self._write_config()
        elif action == "generate":
            self.flow = self._seed_flow()
            self._ensure_task_graph(self.flow)
            self._ensure_wait_edges()
            self._ensure_library()
            self._write_config()
        elif action == "goto":
            node_id = str(payload.get("node_id") or "")
            if not await self.flow_goto(node_id, text=str(payload.get("text") or "")):
                return error_response("节点不存在")
        else:
            return error_response("未知操作")
        return self._ok(flow=self.flow, state=dict(self.state), library=self.library)

    async def api_settings(self):
        payload = await request.json(default={})
        if "theme_market_url" in payload:
            market_url = str(payload.get("theme_market_url") or "").strip().rstrip("/")
            if not market_url.startswith(("http://", "https://")):
                return error_response("市场地址必须是 http/https")
            self.theme_market_url = market_url or "https://shop.swellengine.com/"
        if "auto_reply" in payload:
            self.settings["auto_reply"] = 1 if payload.get("auto_reply") else 0
        audio = payload.get("audio")
        if isinstance(audio, dict):
            for slot, enabled in audio.items():
                if slot in self._action_keys():
                    self.audio[slot] = bool(enabled)
        for key in ("crossfade_ms",):
            if key in payload:
                try:
                    value = int(payload[key])
                except (TypeError, ValueError):
                    continue
                if key == "crossfade_ms":
                    value = max(0, min(3000, value))
                elif key == "scale_percent":
                    value = max(20, min(150, value))
                else:
                    value = max(1, min(600, value))
                self.settings[key] = value
        self._write_config()
        await self._sync_public()
        return self._ok(
            settings=dict(self.settings),
            audio={key: bool(self.audio.get(key, True)) for key in self._action_keys()},
            theme_market_url=self.theme_market_url,
        )

    async def api_slot(self):
        payload = await request.json(default={})
        slot = str(payload.get("slot") or "")
        if slot not in self._action_keys():
            return error_response("未知视频槽位")
        url = str(payload.get("url") or "").strip()
        if not url:
            self._backup_data(f"before-clear-{slot}")
            old = self.videos.pop(slot, "")
            self.durations.pop(slot, None)
            self._remove_local_video(old)
        elif url.startswith(("http://", "https://", "/")):
            self.videos[slot] = url
            self.durations.pop(slot, None)
        else:
            return error_response("视频地址必须是 http(s) 链接或 / 开头的路径")
        self._write_config()
        await self._sync_public()
        return self._ok(slots=self._overview()["slots"])

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
        for slot, url in list(self.videos.items()):
            if not url or url.startswith(("http://", "https://", "/")):
                continue
            if not url.startswith("videos/"):
                continue
            path = self.video_dir / Path(url).name
            if path.is_file() and self._duration(slot) <= 0:
                self.durations[slot] = await self._probe_duration(path)
                changed = True
        if changed:
            self._write_config()

    # ------------------------------------------------------------------ 主题

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
            "actions": [dict(x) for x in self.actions],
            "videos": dict(self.videos),
            "audio": dict(self.audio),
            "durations": dict(self.durations),
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
        self.actions = config["actions"] or self.actions
        self.videos = config["videos"]
        self.audio = config["audio"]
        self.durations = config["durations"]
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
        self._sync_action_slots()
        self._ensure_library()
        self._write_config()
        await self._sync_public()
        await self.force_rest()
        return True

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
        self._hydrate_project_videos(tid, theme)
        config = theme.setdefault("config", {})
        nodes = config.get("flow", {}).setdefault("nodes", [])
        edges = config.get("flow", {}).setdefault("edges", [])
        changed = False
        for node in nodes:
            if node.get("role") == "rest" and node.get("loop"):
                node["role"] = "wait"
                changed = True
        pickup = next((n for n in nodes if n.get("id") == "phone_pickup"), None)
        if pickup is None:
            pickup = next((n for n in nodes if n.get("role") == "phone"), None)
        if pickup is not None:
            for node in nodes:
                if node.get("role") != "wait":
                    continue
                if any(e.get("from") == node.get("id") for e in edges):
                    continue
                edges.append(
                    {
                        "id": f"e_{node.get('id')}_msg",
                        "from": node.get("id"),
                        "to": pickup.get("id"),
                        "label": "提前收到消息",
                    }
                )
                changed = True
        before_graph = json.dumps(config.get("flow") or {}, ensure_ascii=False)
        self._ensure_task_graph(config.get("flow") or {})
        if before_graph != json.dumps(config.get("flow") or {}, ensure_ascii=False):
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
            (project / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if bool(payload.get("apply")) and tid == self.active_theme_id:
                await self._apply_theme_id(tid)
            return self._ok(project=self._project_payload(tid))
        if action == "asset_delete":
            project = self._project_dir(tid)
            if not project:
                return error_response("项目不存在")
            theme = theme_store.read_theme_json(project / "theme.json")
            media_id = str(payload.get("media_id") or "")
            used = [
                n.get("id")
                for n in (theme.get("config", {}).get("flow", {}).get("nodes") or [])
                if str(n.get("media_id") or "") == media_id
            ]
            if used:
                return error_response("素材还被节点使用：" + "、".join(used[:5]))
            config = theme.get("config") or {}
            item = next((x for x in config.get("library") or [] if x.get("id") == media_id), None)
            if item is None:
                return error_response("素材不存在")
            config["library"] = [x for x in config.get("library") or [] if x.get("id") != media_id]
            theme["config"] = config
            (project / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            local = str(item.get("video") or "")
            if local.startswith("videos/"):
                (project / local).unlink(missing_ok=True)
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
                target = target_dir / f"{tid}.fishtheme"
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
            (self.themes_dir / f"{tid}.fishtheme").unlink(missing_ok=True)
            if self.public_dir:
                shutil.rmtree(self.public_dir / "themes" / "projects" / tid, ignore_errors=True)
                for cover in (self.public_dir / "themes" / "covers").glob(f"{tid}.*"):
                    cover.unlink(missing_ok=True)
            if self.active_theme_id == tid:
                self.active_theme_id = ""
                self._write_config()
            return self._ok(themes=self._theme_list())
        return error_response("未知操作")

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
                target = target_dir / f"{theme['meta']['id']}.fishtheme"
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
            (self.themes_dir / f"{tid}.fishtheme").unlink(missing_ok=True)
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

    async def api_theme_market(self):
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
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
                    (self.themes_dir / f"{existing['id']}.fishtheme").unlink(missing_ok=True)
                raw = await asyncio.to_thread(
                    self._http_sync, f"{base}/api/themes/{theme_id}/download", "GET", token
                )
                if not isinstance(raw, (bytes, bytearray)):
                    return error_response("下载失败：服务器没有返回文件")
                temp = self.themes_dir / f"market_{uuid.uuid4().hex[:8]}.fishtheme"
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
                    theme["meta"]["name"] = f"{incoming_name} ({suffix})"
                    theme_file.write_text(
                        json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                if bool(payload.get("apply")):
                    await self._apply_theme_id(installed["id"])
                return self._ok(
                    theme_id=installed["id"],
                    theme=installed["theme"]["meta"],
                    themes=self._theme_list(),
                    **({"applied": True} if payload.get("apply") else {}),
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
            if action == "update_market":
                if not token:
                    return error_response("请先登录市场")
                theme_id = str(payload.get("id") or "").strip()
                body = {
                    key: payload[key]
                    for key in ("name", "description", "version")
                    if payload.get(key) is not None
                }
                result = await asyncio.to_thread(
                    self._http_sync,
                    f"{base}/api/themes/{theme_id}",
                    "PATCH",
                    token,
                    body,
                )
                return self._ok(item=result, url=base)
            return error_response("未知操作")
        except Exception as exc:
            return error_response(str(exc))

    async def api_upload_start(self):
        payload = await request.json(default={})
        slot = str(payload.get("slot") or "")
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
        elif to_theme:
            pass
        elif to_library:
            pass
        elif node_id:
            if self._flow_node(node_id) is None:
                return error_response("未知流程图节点")
        elif slot not in self._action_keys():
            return error_response("未知视频槽位")
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
            "slot": slot,
            "node_id": node_id,
            "library": to_library,
            "theme": to_theme,
            "project_asset": to_project_asset,
            "cover": to_cover,
            "project_id": project_id,
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
                stem = _safe_name(str(info.get("filename") or "asset"), "asset")[:30]
                target_name = f"asset_{int(time.time())}_{stem}{info['ext']}"
                target = project / "videos" / target_name
                target.parent.mkdir(parents=True, exist_ok=True)
                info["part"].replace(target)
                mid = f"m_{uuid.uuid4().hex[:8]}"
                item = {
                    "id": mid,
                    "name": str(info.get("filename") or Path(target_name).stem)[:40],
                    "video": f"videos/{target_name}",
                    "duration": await self._probe_duration(target),
                }
                theme.setdefault("config", {}).setdefault("library", []).append(item)
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
                theme.setdefault("meta", {})["cover"] = cover_name
                (project / "theme.json").write_text(
                    json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return self._ok(project=self._project_payload(str(info.get("project_id"))))
            if info.get("theme"):
                target = self.themes_dir / f"upload_{int(time.time())}_{_safe_name(str(info.get('filename') or 'theme'))}.fishtheme"
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
                stem = _safe_name(str(info.get("filename") or "video"), "media")[:30]
                target_name = f"media_{int(time.time())}_{stem}{info['ext']}"
                target = self.video_dir / target_name
                info["part"].replace(target)
                mid = f"m_{uuid.uuid4().hex[:8]}"
                item = {
                    "id": mid,
                    "name": str(info.get("filename") or Path(target_name).stem)[:40],
                    "video": f"videos/{target_name}",
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
                target_name = f"node_{_safe_name(node_id)}_{int(time.time())}{info['ext']}"
                target = self.video_dir / target_name
                info["part"].replace(target)
                node["video"] = f"videos/{target_name}"
                node["duration"] = await self._probe_duration(target)
                self._write_config()
                return self._ok(
                    node_id=node_id,
                    url=node["video"],
                    duration=node["duration"],
                    flow=self.flow,
                )
            target_name = f"{info['slot']}_{int(time.time())}{info['ext']}"
            target = self.video_dir / target_name
            old = self.videos.get(info["slot"], "")
            if old:
                self._backup_data(f"before-replace-{info['slot']}")
            info["part"].replace(target)
            self.videos[info["slot"]] = f"videos/{target_name}"
            self.durations[info["slot"]] = await self._probe_duration(target)
            self._write_config()
            if old and old != self.videos[info["slot"]]:
                self._remove_local_video(old)
            await self._sync_public()
            return self._ok(
                slot=info["slot"],
                url=self.videos[info["slot"]],
                slots=self._overview()["slots"],
            )
        except Exception as exc:
            info["part"].unlink(missing_ok=True)
            logger.warning(f"大肥鱼的小屋保存视频失败: {exc}")
            return error_response("保存视频失败")

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
            return self._ok(restored=backup_id, videos=dict(self.videos))
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋恢复备份失败: {exc}")
            return error_response("恢复备份失败")

    async def api_sync(self):
        await self._sync_public()
        return self._ok(
            public_url=self._public_url(),
            public_ready=bool(self.public_dir and (self.public_dir / "index.html").is_file()),
        )

"""大肥鱼的小屋：视频桌宠 + 状态机 + 公共页面。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

PLUGIN_NAME = "astrbot_plugin_fish_house"
VERSION = "1.9.2"

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
        self._go_token = 0
        self._last_activity = ""
        self.videos: dict[str, str] = {}
        self.durations: dict[str, float] = {}
        self.audio: dict[str, bool] = {key: True for key in SLOT_KEYS}
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

    def _load(self) -> None:
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                if isinstance(data.get("videos"), dict):
                    self.videos = {
                        key: str(value)
                        for key, value in data["videos"].items()
                        if key in SLOT_KEYS and value
                    }
                if isinstance(data.get("durations"), dict):
                    self.durations = {
                        key: float(value or 0)
                        for key, value in data["durations"].items()
                        if key in SLOT_KEYS
                    }
                if isinstance(data.get("audio"), dict):
                    for key in SLOT_KEYS:
                        if key in data["audio"]:
                            self.audio[key] = bool(data["audio"][key])
                if isinstance(data.get("settings"), dict):
                    self.settings.update(data["settings"])
            except Exception as exc:
                logger.warning(f"大肥鱼的小屋读取配置失败: {exc}")
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
            "audio": {key: bool(self.audio.get(key, True)) for key in SLOT_KEYS},
            "crossfade_ms": int(self.settings.get("crossfade_ms", 400)),
            "title": "大肥鱼的小屋",
            "updated_at": time.time(),
        }

    def _write_config(self) -> None:
        self._write_json(
            self.config_file,
            {
                "videos": dict(self.videos),
                "audio": {key: bool(self.audio.get(key, True)) for key in SLOT_KEYS},
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
                    if path.suffix not in {".py", ".yaml", ".yml", ".json", ".md", ".html", ".js", ".css"}:
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

    async def initialize(self) -> None:
        self._backup_data("before-update")
        self._install_builtin_videos()
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
                }
            )
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

    async def _rest_after_delay(self, delay: float) -> None:
        """兜底：用户说要休息后，如果消息流程没触发，也在这段时间后回去。"""
        try:
            await asyncio.sleep(max(1.0, float(delay)))
            if self.state.get("mode") in {"computer", "phone"}:
                await self.work_end(force=True)
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
            activity = self._auto_activity(event.message_str)
            await self.go_computer(
                umo=event.unified_msg_origin,
                activity=activity,
            )
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋自动去电脑失败: {exc}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
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
            if mode in {"computer", "phone"} and self._looks_like_rest(
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
                "长任务每 30-50 秒调用 house_work_ping 续期，超过一分钟没任务会自动回休息。"
                "只闲聊时不用调用。活动气泡要像你本人在说话，例如「帮主人查资料」「我去逛一下抖音」。"
            )
            current = str(getattr(req, "system_prompt", "") or "")
            if "house_work_start" not in current:
                req.system_prompt = current + prompt
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋注入提示失败: {exc}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """回复先等桌宠拿到手机，再真正发出去。"""
        try:
            if not self.public_prefix_enabled():
                return
            if event.get_extra("_fish_rest_now"):
                # 用户要求回去休息：这条回复直接发，不再等手机/加延迟。
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
        """可选的自动钩子：Bot 发消息时播手机动画。

        干活状态不再从消息内容猜测，统一由 /event 接口或面板触发。
        """
        try:
            if not self.public_prefix_enabled():
                return
            if event.get_extra("_fish_rest_now"):
                # 回复已经发出，现在再回休息（不会闪出拿手机动画）。
                await self.work_end(force=True)
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

    def _overview(self) -> dict:
        slots = []
        for key, label, hint in SLOTS:
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
        else:
            return error_response("未知动作")
        return self._ok(state=dict(self.state))

    async def api_settings(self):
        payload = await request.json(default={})
        if "auto_reply" in payload:
            self.settings["auto_reply"] = 1 if payload.get("auto_reply") else 0
        audio = payload.get("audio")
        if isinstance(audio, dict):
            for slot, enabled in audio.items():
                if slot in SLOT_KEYS:
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
            audio={key: bool(self.audio.get(key, True)) for key in SLOT_KEYS},
        )

    async def api_slot(self):
        payload = await request.json(default={})
        slot = str(payload.get("slot") or "")
        if slot not in SLOT_KEYS:
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

    async def api_upload_start(self):
        payload = await request.json(default={})
        slot = str(payload.get("slot") or "")
        filename = str(payload.get("filename") or "")
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if slot not in SLOT_KEYS:
            return error_response("未知视频槽位")
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            return error_response("只支持 mp4 / webm / m4v / mov 视频")
        max_bytes = int(self._cfg("max_upload_mb", 200)) * 1024 * 1024
        if size <= 0 or size > max_bytes:
            return error_response(f"视频大小需在 1 字节到 {max_bytes // 1024 // 1024}MB 之间")
        upload_id = uuid.uuid4().hex
        part = self.upload_dir / f"{upload_id}.part"
        part.write_bytes(b"")
        self._uploads[upload_id] = {
            "slot": slot,
            "ext": ext,
            "size": size,
            "received": 0,
            "index": 0,
            "part": part,
        }
        return self._ok(upload_id=upload_id, chunk_size=128 * 1024)

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

"""壁纸状态：画布节点、气泡与持久化。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from astrbot.api import logger


class StateMixin:
    async def _set_state(
        self,
        event: str,
        *,
        bubble: str | None = None,
        bubble_until: float | None = None,
        bubble_source: str = "auto",
        node_id: str | None = None,
        node_video: str | None = None,
        node_loop: bool = False,
        node_label: str | None = None,
    ) -> int:
        async with self._lock:
            self.state.update(
                {
                    "mode": "flow",
                    "event": event,
                    "updated_at": time.time(),
                    "node_id": str(node_id or ""),
                    "node_video": str(node_video or ""),
                    "node_loop": bool(node_loop),
                    "node_label": str(node_label or ""),
                }
            )
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
        return seq

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

    async def note_playing(self, node_id: str) -> None:
        """壁纸页确认节点视频真正开始播放后回报，面板据此高亮正在播放的节点。"""
        node_id = str(node_id or "").strip()
        if not node_id:
            return
        now = time.time()
        async with self._lock:
            if (
                self.state.get("playing_node") == node_id
                and now - float(self.state.get("playing_at") or 0) < 2
            ):
                return
            self.state["playing_node"] = node_id
            self.state["playing_at"] = now
            self.state["seq"] = int(self.state.get("seq") or 0) + 1
            self._write_state_locked()

    async def force_rest(self) -> None:
        """回到画布的第一个循环节点；画布为空时只清空播放状态。"""
        node = self._start_node()
        if node is not None:
            await self.flow_goto(str(node.get("id") or ""))
            return
        self.state["bubble"] = ""
        self.state["bubble_until"] = 0
        self.state["bubble_source"] = ""
        await self._set_state("rest")

    def _node_video_ready(self, node: dict) -> bool:
        """节点指向的视频是否真的在 videos/ 里，旧主题的残留节点会失败。"""
        try:
            name = Path(self._node_video(node)).name
        except Exception:
            return False
        return bool(name) and (self.video_dir / name).is_file()

    def _restore_flow_state(self) -> None:
        """重启后如果之前停在画布节点，继续播放该节点。"""
        try:
            if not self.state_file.is_file():
                return
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("mode") != "flow":
                return
            node = self._flow_node(str(saved.get("node_id") or ""))
            # 换过主题/项目后旧节点在新画布上已经不存在，或者它引用的视频
            # 已被清理，这时直接回到当前画布的第一个循环节点。
            if node is None or not self._node_video_ready(node):
                node = self._start_node()
            if node is None:
                return
            self.state.update(
                mode="flow",
                node_id=str(node.get("id") or ""),
                node_video=self._node_video(node),
                node_loop=bool(node.get("loop")),
                node_label=str(node.get("label") or node.get("id") or ""),
                updated_at=time.time(),
            )
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋恢复画布状态失败: {exc}")

"""画布流程图、素材库与节点播放。"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from pathlib import Path
from typing import Any


class FlowMixin:
    @staticmethod
    def _flow_has_loop(flow: dict[str, Any]) -> bool:
        return any(
            node.get("role") in {"wait", "both", "work", "phone"} and node.get("loop")
            for node in (flow.get("nodes") or [])
        )

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
                role = str(item.get("role") or "action")[:20]
                nodes.append(
                    {
                        "id": nid,
                        "label": str(item.get("label") or nid)[:40],
                        "video": str(item.get("video") or ""),
                        "loop": False if role in {"both_done", "work_done"} else bool(item.get("loop")),
                        "role": role,
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
        """空画布只保留一个循环等待节点，视频由主题/项目自己配。"""
        return {
            "nodes": [
                {
                    "id": "wait_1",
                    "label": "循环等待",
                    "video": "",
                    "loop": True,
                    "role": "wait",
                    "prompt": "",
                    "note": "",
                    "duration": 0.0,
                    "media_id": "",
                    "x": 420.0,
                    "y": 220.0,
                }
            ],
            "edges": [],
        }

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

    def _start_node(self) -> dict[str, Any] | None:
        """画布的起点：第一个循环节点，没有循环节点就用第一个节点。"""
        nodes = self.flow.get("nodes") or []
        if not nodes:
            return None
        return next(
            (
                node
                for node in nodes
                if node.get("role") in {"wait", "both", "work", "phone"} and node.get("loop")
            ),
            nodes[0],
        )

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
        """把画布节点里的视频收进素材库，并给节点补 media_id。"""
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

        for node in self.flow.get("nodes", []):
            item = add(
                str(node.get("video") or ""),
                str(node.get("label") or ""),
                float(node.get("duration") or 0),
            )
            if item:
                node["media_id"] = item["id"]

    async def _refresh_current_node(self) -> None:
        """画布保存后，把当前节点的素材和名称同步到直播页。"""
        nid = str(self.state.get("node_id") or "")
        if not nid:
            return
        node = self._flow_node(nid)
        if node is None:
            fallback = next(
                (item for item in self.flow.get("nodes") or [] if item.get("loop")),
                None,
            )
            if fallback is not None:
                await self.flow_goto(str(fallback.get("id") or ""))
            return
        video = self._node_video(node)
        label = str(node.get("label") or nid)
        loop = bool(node.get("loop"))
        bubble = self._bubble_text(node)
        if (
            video == str(self.state.get("node_video") or "")
            and label == str(self.state.get("node_label") or "")
            and loop == bool(self.state.get("node_loop"))
            and bubble == str(self.state.get("bubble") or "")
        ):
            return
        await self._set_state(
            "flow_node",
            bubble=bubble,
            node_id=nid,
            node_video=video,
            node_loop=loop,
            node_label=label,
        )

    def _bubble_text(self, node: dict) -> str:
        """节点气泡兜底文案：提示词 > 备注 > 节点名（模型生成失败时使用）。"""
        raw = (
            str(node.get("prompt") or "").strip()
            or str(node.get("note") or "").strip()
            or str(node.get("label") or "").strip()
        )
        return " ".join(raw.split())[:40]

    async def _generate_bubble_text(self, node: dict) -> str:
        """每次进入节点都让模型现写一句气泡，失败时回落到节点文案。"""
        label = str(node.get("label") or "").strip() or "发呆"
        hint = str(node.get("prompt") or node.get("note") or "").strip()
        fallback = self._bubble_text(node)
        try:
            provider = self.context.get_using_provider()
        except Exception:
            provider = None
        if provider is None:
            return fallback
        recent = list(getattr(self, "_recent_bubbles", []) or [])
        style = random.choice(
            [
                "写正在做的小动作",
                "写当下的心情",
                "写一句小声吐槽",
                "写一句期待或打算",
                "写一句对主人的招呼",
            ]
        )
        ask = (
            f"当前动作：{label}\n"
            + (f"动作说明：{hint}\n" if hint else "")
            + f"这次请{style}。用第一人称，不超过 12 个字，口语化。\n"
        )
        if recent:
            ask += "不要和下面这些已用过的句子重复或近似：\n" + "\n".join(f"- {x}" for x in recent) + "\n"
        ask += "只输出这一句话。"
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=ask,
                    system_prompt="你在扮演桌面壁纸里的小角色。只输出一句中文气泡，不要引号、不要解释。",
                ),
                timeout=15,
            )
            text = str(getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            text = ""
        text = text.splitlines()[0].strip().strip("“”\"'") if text else ""
        text = text[:20]
        if text:
            recent.append(text)
            self._recent_bubbles = recent[-8:]
        return text or fallback

    async def _bubble_after_enter(self, node_id: str) -> None:
        node = self._flow_node(node_id)
        if node is None:
            return
        text = await self._generate_bubble_text(node)
        if not text or self.state.get("node_id") != node_id:
            return
        # 气泡强制每次都由模型生成，只展示 3 秒
        await self.set_bubble(text, ttl_seconds=3)

    async def flow_goto(self, node_id: str, *, text: str = "", umo: str = "") -> bool:
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            return False
        nid = node["id"]
        video = self._node_video(node)
        # 气泡每次都由模型现生成，进入节点后异步补上。
        await self._set_state(
            "flow_node",
            bubble=text or "",
            node_id=nid,
            node_video=video,
            node_loop=bool(node.get("loop")),
            node_label=str(node.get("label") or nid),
        )
        bubble_task = asyncio.create_task(self._bubble_after_enter(nid))
        self._tasks.add(bubble_task)
        bubble_task.add_done_callback(self._tasks.discard)
        # 一次性节点播完自动接下一节点；回消息这类循环节点播一轮后也放下手机。
        if not node.get("loop") or node.get("role") == "phone":
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
            if outgoing:
                # 一次性节点播完自动接下一节点；多条出边默认走第一条，模型可在后续消息里改。
                await self.flow_goto(str(outgoing[0].get("to") or ""))
        except asyncio.CancelledError:
            return

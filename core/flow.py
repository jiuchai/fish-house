"""画布流程图、素材库与节点播放。"""

from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger


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

    def queue_goto(self, node_id: str) -> bool:
        """记下本轮想去的目的地，等自动流程走到包含它的分叉时再切。"""
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            return False
        self._pending_next = str(node.get("id") or "")
        return True

    def _flow_preview(self, start_id: str, depth: int = 5) -> tuple[list[str], dict | None]:
        """沿唯一出边往下看，返回会自动经过的节点名和第一个分叉节点。"""
        chain: list[str] = []
        node = self._flow_node(start_id)
        seen = {start_id}
        while node is not None and len(chain) < depth:
            outgoing = self._flow_outgoing(str(node.get("id") or ""))
            if len(outgoing) != 1:
                return chain, node if len(outgoing) > 1 else None
            target = self._flow_node(str(outgoing[0].get("to") or ""))
            if target is None or str(target.get("id") or "") in seen:
                return chain, None
            seen.add(str(target.get("id") or ""))
            chain.append(f"{target.get('id')}={target.get('label')}")
            node = target
        return chain, None

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
        if nid == self._pending_next:
            # 已经走到模型选的目标，这条记录用完就清掉，免得影响后面的分叉。
            self._pending_next = ""
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
        # 一次性节点播完自动接下一节点；回消息这类节点播一轮后再看要不要走。
        if not node.get("loop") or node.get("role") == "phone":
            duration = float(node.get("duration") or 0)
            if duration <= 0:
                item = self._library_item(str(node.get("media_id") or ""))
                duration = float((item or {}).get("duration") or 0)
            if duration <= 0:
                duration = 5.0
            self.schedule_advance(nid, duration + 0.25)
        elif node.get("role") in {"work", "both"}:
            # 循环工作节点：空闲超时自动走向下一步。
            self._schedule_idle(nid)
        return True

    def schedule_advance(self, node_id: str, delay: float) -> None:
        task = asyncio.create_task(self._flow_advance_after(node_id, delay))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def idle_timeout(self) -> float:
        """工作/回消息这类循环节点空闲多久（秒）自动走向下一步。"""
        return 60.0

    def _touch_idle(self) -> None:
        """收到新消息时推迟当前循环工作节点的空闲截止时间。"""
        node = self._flow_node(str(self.state.get("node_id") or ""))
        if node is None or not node.get("loop") or node.get("role") not in {"work", "both"}:
            return
        self._idle_deadline = time.time() + self.idle_timeout()

    def _schedule_idle(self, node_id: str) -> None:
        """进入循环工作节点后，空闲超时自动走向下一步。"""
        self._idle_deadline = time.time() + self.idle_timeout()
        task = getattr(self, "_idle_task", None)
        if task is not None and not task.done():
            task.cancel()
        task = asyncio.create_task(self._idle_watch(node_id))
        self._idle_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _idle_watch(self, node_id: str) -> None:
        try:
            while True:
                remaining = float(getattr(self, "_idle_deadline", 0) or 0) - time.time()
                if remaining > 0:
                    await asyncio.sleep(min(remaining, 1.0))
                    continue
                if self.state.get("mode") != "flow" or str(self.state.get("node_id") or "") != node_id:
                    return
                node = self._flow_node(node_id)
                if node is None or not node.get("loop") or node.get("role") not in {"work", "both"}:
                    return
                outgoing = self._flow_outgoing(node_id)
                if not outgoing:
                    return
                logger.info(f"大肥鱼画布：工作节点 {node_id} 空闲超时，自动走向下一步")
                await self._flow_advance_after(node_id, 0)
                return
        except asyncio.CancelledError:
            return

    def _flow_next_toward(self, node_id: str, target: str, depth: int = 4) -> str:
        """在 node_id 的出边里找一条能在几步内走到 target 的，用来回到工作节点。"""
        for edge in self._flow_outgoing(node_id):
            first = str(edge.get("to") or "")
            if not first:
                continue
            if first == target:
                return first
            seen = {node_id, first}
            frontier = [first]
            for _ in range(max(0, depth - 1)):
                following: list[str] = []
                for cur in frontier:
                    for nxt in self._flow_outgoing(cur):
                        to = str(nxt.get("to") or "")
                        if not to or to in seen:
                            continue
                        if to == target:
                            return first
                        seen.add(to)
                        following.append(to)
                frontier = following
                if not frontier:
                    break
        return ""

    def _flow_reaches(self, node_id: str, target: str, depth: int = 12) -> bool:
        """从 node_id 顺着连线往下走，能不能走到 target。"""
        seen = {node_id}
        frontier = [node_id]
        for _ in range(max(0, depth)):
            following: list[str] = []
            for cur in frontier:
                for edge in self._flow_outgoing(cur):
                    to = str(edge.get("to") or "")
                    if not to or to in seen:
                        continue
                    if to == target:
                        return True
                    seen.add(to)
                    following.append(to)
            frontier = following
            if not frontier:
                break
        return False

    async def _flow_advance_after(self, node_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.5, delay))
            if self.state.get("mode") != "flow" or self.state.get("node_id") != node_id:
                return
            outgoing = self._flow_outgoing(node_id)
            if not outgoing:
                return
            pending = str(self._pending_next or "")
            back = str(self._return_to or "")
            if pending and pending != node_id:
                targets = {str(item.get("to") or "") for item in outgoing}
                # 模型选的目标不在后面的连线上：这一条播完就直接跳过去。
                if (
                    pending not in targets
                    and self._flow_node(pending) is not None
                    and not self._flow_reaches(node_id, pending)
                ):
                    self._pending_next = ""
                    await self.flow_goto(pending)
                    return
            if len(outgoing) > 1:
                # 多条出边时：模型这轮选的目标 > 回消息前的来源 > 能走到它们的那条 > 第一条。
                chosen = next(
                    (item for item in outgoing if str(item.get("to") or "") == pending),
                    None,
                )
                if chosen is None and pending:
                    # 目标在后面但不在这一条出边上，顺着连线一步步走过去。
                    toward = self._flow_next_toward(node_id, pending)
                    chosen = next(
                        (item for item in outgoing if str(item.get("to") or "") == toward),
                        None,
                    )
                    if chosen is None:
                        # 后面根本走不到，直接跳过去，不能卡在这儿。
                        self._pending_next = ""
                        self._return_to = ""
                        await self.flow_goto(pending)
                        return
                if chosen is None and back:
                    chosen = next(
                        (item for item in outgoing if str(item.get("to") or "") == back),
                        None,
                    )
                    if chosen is None:
                        toward = self._flow_next_toward(node_id, back)
                        chosen = next(
                            (item for item in outgoing if str(item.get("to") or "") == toward),
                            None,
                        )
                if chosen is not None and str(chosen.get("to") or "") == pending:
                    self._pending_next = ""
                self._return_to = ""
                await self.flow_goto(str((chosen or outgoing[0]).get("to") or ""))
                return
            await self.flow_goto(str(outgoing[0].get("to") or ""))
        except asyncio.CancelledError:
            return

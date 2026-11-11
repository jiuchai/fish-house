"""大肥鱼的小屋：主题画布视频壁纸 + 面板服务。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .api.base import ApiBaseMixin
from .api.content import ContentApiMixin
from .api.market import MarketApiMixin
from .api.system import SystemApiMixin
from .api.theme import ThemeApiMixin
from .api.upload import UploadApiMixin
from .core.config import ConfigMixin
from .core.constants import DEFAULT_SETTINGS, PLUGIN_NAME, PLUGIN_ROOT
from .core.flow import FlowMixin
from .core.server import PanelServer
from .core.state import StateMixin
from .core.storage import StorageMixin
from .core.themes import ThemeMixin


class FishHousePlugin(
    ConfigMixin,
    FlowMixin,
    StateMixin,
    StorageMixin,
    ThemeMixin,
    ApiBaseMixin,
    SystemApiMixin,
    ContentApiMixin,
    ThemeApiMixin,
    MarketApiMixin,
    UploadApiMixin,
    Star,
):
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
        self._lock = asyncio.Lock()
        self.server_port = int(self._cfg("panel_port", 47613) or 0)
        self.server = PanelServer(self)
        self._tasks: set[asyncio.Task] = set()
        self._uploads: dict[str, dict[str, Any]] = {}
        self.market_progress: dict[str, Any] = {
            "active": False, "phase": "", "received": 0, "total": 0, "error": ""
        }
        self.market_cancel = False
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
            "mode": "flow",
            "seq": 1,
            "updated_at": time.time(),
            "event": "init",
            "bubble": "",
            "bubble_until": 0,
            "bubble_source": "",
            "node_id": "",
            "node_video": "",
            "node_loop": False,
            "node_label": "",
            "playing_node": "",
            "playing_at": 0,
        }
        self.public_root: Path | None = None
        self.public_dir: Path | None = None
        self.export_file: Path | None = None
        self._load()
        self._detect_public_root()

    async def terminate(self) -> None:
        """重载/卸载插件时释放控制台端口。

        terminate 写在 mixin 里不算插件类自己的方法，AstrBot 卸载插件时
        只查插件类自身的 __dict__，所以这里必须显式转发一次，否则旧实例
        会一直占着端口，新实例绑不上，面板读到的还是旧状态。
        """
        await StorageMixin.terminate(self)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """收到消息时推进等待节点，并把流程图交给模型。"""
        if not self._can_feedback(event):
            return
        try:
            if not event.is_private_chat() or not self.public_prefix_enabled():
                return
            if not event.get_extra("_fish_flow_advanced"):
                # 循环节点只有唯一出边时自动前进；多条分支交给模型按节点说明选择。
                # 一次用户消息只推进一次，避免工具调用多轮重复触发。
                current = self._flow_node(str(self.state.get("node_id") or ""))
                if (
                    current is not None
                    and current.get("loop")
                    and current.get("role") != "phone"
                ):
                    cur_id = str(current.get("id") or "")
                    event.set_extra("_fish_flow_from", cur_id)
                    outgoing = self._flow_outgoing(cur_id)
                    if len(outgoing) == 1:
                        target_id = str(outgoing[0].get("to") or "")
                        if target_id and target_id != cur_id:
                            event.set_extra("_fish_flow_advanced", True)
                            await self.flow_goto(target_id)
                    elif outgoing:
                        logger.info(f"大肥鱼画布：等待模型从 {cur_id} 选择下一步")
            prompt = (
                "\n【大肥鱼壁纸】你在私聊里有一张会动的桌面壁纸，动作由当前主题的画布控制。"
                "收到用户消息时，循环节点如果只有一条出边会自动前进。"
                "停在有多个分支的节点时，必须根据用户这句话和每个节点的说明，"
                "调用 house_flow_goto(node_id=节点id) 从出边里选择一条，不要只回复不切换。"
                "活动气泡要像你本人在说话，例如「帮主人查资料」，用 house_activity 更新。"
            )
            current = str(getattr(req, "system_prompt", "") or "")
            if "【大肥鱼壁纸】" not in current:
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
                flow_hint += (
                    "收到新消息时，必须从可选下一步中选一条并调用 house_flow_goto；"
                    "没有可选下一步就保持当前节点。"
                )
                if "【流程图】" not in current:
                    current += flow_hint
            req.system_prompt = current
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋注入提示失败: {exc}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """把回信压到壁纸走到「回消息」时再发，保证画面和 QQ 消息同步。"""
        if not self._can_feedback(event):
            return
        try:
            if not event.is_private_chat() or not self.public_prefix_enabled():
                return
            from_id = str(event.get_extra("_fish_flow_from") or "")
            if not from_id:
                return
            if not any(
                item.get("role") == "phone" for item in self.flow.get("nodes") or []
            ):
                return
            if str(self.state.get("node_id") or "") == from_id:
                # 模型没选分支：先按第一条出边走过去，再等它拿起手机。
                current = self._flow_node(from_id)
                outgoing = (
                    self._flow_outgoing(from_id)
                    if current is not None and current.get("loop")
                    else []
                )
                if outgoing:
                    await self.flow_goto(str(outgoing[0].get("to") or ""))
            deadline = time.time() + 10
            while time.time() < deadline:
                node = self._flow_node(str(self.state.get("node_id") or ""))
                if node is None:
                    return
                role = str(node.get("role") or "")
                if role == "phone":
                    return
                if role in {"action", "transition"}:
                    nxt = self._flow_outgoing(str(node.get("id") or ""))
                    target = (
                        self._flow_node(str(nxt[0].get("to") or ""))
                        if len(nxt) == 1
                        else None
                    )
                    if target is None or target.get("role") != "phone":
                        return
                    await asyncio.sleep(0.2)
                    continue
                return
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋等待回消息失败: {exc}")

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent) -> None:
        """模型没有选择下一步时，兜底走第一条出边，避免画布卡在等待节点。"""
        if not self._can_feedback(event):
            return
        try:
            if not event.is_private_chat() or not self.public_prefix_enabled():
                return
            from_id = str(event.get_extra("_fish_flow_from") or "")
            event.set_extra("_fish_flow_from", "")
            if not from_id or str(self.state.get("node_id") or "") != from_id:
                return
            current = self._flow_node(from_id)
            if current is None or not current.get("loop"):
                return
            outgoing = self._flow_outgoing(from_id)
            if not outgoing:
                return
            if await self.flow_goto(str(outgoing[0].get("to") or "")):
                logger.info(f"大肥鱼画布兜底推进：{from_id} -> {outgoing[0].get('to')}")
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋兜底推进失败: {exc}")

    @filter.llm_tool(name="house_activity")
    async def tool_activity(self, event: AstrMessageEvent, activity: str, ttl_seconds: int = 0) -> str:
        """设置壁纸底部的活动气泡。做任务时用一句自然的话告诉用户你在干什么，例如「帮主人查资料」「我去逛一下抖音」「在整理今天的账单」。不要每轮都说，只在开始做某件事时更新。

        Args:
            activity(string): 展示在壁纸底部气泡里的一句话，尽量 20 字以内
            ttl_seconds(number): 多少秒后自动消失；0 表示一直保留到下次更新
        """
        if not event.is_private_chat():
            return "活动气泡只在私聊中设置。"
        text = str(activity or "").strip()
        if not text:
            return "气泡内容不能为空。"
        await self.set_bubble(text, int(ttl_seconds or 0))
        return f"气泡已更新：{text}"

    @filter.llm_tool(name="house_flow_goto")
    async def tool_flow_goto(self, event: AstrMessageEvent, node_id: str) -> str:
        """按流程图切换壁纸当前动作。node_id 必须是流程图里存在的节点 id。

        Args:
            node_id(string): 要切换到的节点 id，例如「wait_1」
        """
        if not event.is_private_chat():
            return "流程图动作只在私聊中启用。"
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            ids = "、".join(str(item.get("id")) for item in self.flow.get("nodes", []))
            return f"没有这个节点。可用节点：{ids}"
        await self.flow_goto(node["id"], umo=event.unified_msg_origin)
        return f"已切到「{node.get('label') or node['id']}」。"

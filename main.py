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
        # 模型提前选好的后续分支目标，等流程走到对应分叉时再切过去。
        self._pending_next = ""
        # 从工作/休息等循环动作临时去「回消息」时记下来源，回完自动返回。
        self._return_to = ""
        # 循环工作节点的空闲截止时间（秒），到了就自动走向下一步。
        self._idle_deadline = 0.0
        self._idle_task: asyncio.Task | None = None
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
            if not event.get_extra("_fish_turn_started"):
                event.set_extra("_fish_turn_started", True)
                # 每轮只重置模型这轮选的目标；回工作的来源留着，免得回消息期间再来一条消息就丢了。
                self._pending_next = ""
                # 收到消息说明有活动，推迟当前循环工作节点的空闲截止时间。
                self._touch_idle()
            if not event.get_extra("_fish_flow_advanced"):
                # 等待节点收到消息自动前进；工作/休息等循环动作先播「回消息」再自动返回。
                # 一次用户消息只推进一次，避免工具调用多轮重复触发。
                current = self._flow_node(str(self.state.get("node_id") or ""))
                if (
                    current is not None
                    and current.get("loop")
                    and current.get("role") != "phone"
                ):
                    cur_id = str(current.get("id") or "")
                    outgoing = self._flow_outgoing(cur_id)
                    if current.get("role") == "wait":
                        event.set_extra("_fish_flow_from", cur_id)
                        if len(outgoing) == 1:
                            target_id = str(outgoing[0].get("to") or "")
                            if target_id and target_id != cur_id:
                                event.set_extra("_fish_flow_advanced", True)
                                await self.flow_goto(target_id)
                        elif outgoing:
                            logger.info(f"大肥鱼画布：等待模型从 {cur_id} 选择下一步")
                    elif current.get("role") == "both":
                        # 这个节点本身就能回消息也能干活，直接在原地回，不用跳动画。
                        logger.info(
                            f"大肥鱼画布：停在可回消息的工作节点 {cur_id}，原地回复"
                        )
                    else:
                        phone = ""
                        for edge in outgoing:
                            target = self._flow_node(str(edge.get("to") or ""))
                            if target is not None and target.get("role") == "phone":
                                phone = str(target.get("id") or "")
                                break
                        if not phone:
                            # 没有直连回消息节点时，退回主题里唯一的回消息节点。
                            for item in self.flow.get("nodes") or []:
                                if item.get("role") == "phone":
                                    phone = str(item.get("id") or "")
                                    break
                        if phone and phone != cur_id:
                            self._return_to = cur_id
                            event.set_extra("_fish_flow_from", cur_id)
                            event.set_extra("_fish_flow_advanced", True)
                            await self.flow_goto(phone)
            prompt = (
                "\n【大肥鱼壁纸】你在私聊里有一张会动的桌面壁纸，你能在里面做什么由当前主题的画布控制。"
                "画布上的节点各管一件事：只有专门干活的节点（名字或说明里写着干活、工作的）才接任务；"
                "「收到消息」「放下手机」这类过场动作、吃零食/休息这类待机节点、以及只负责回消息的节点，"
                "都只是演动画，不能在这些节点上把任务做完。"
                "只要用户这句话是让你做事（找东西、翻东西、查资料、删文件、写东西、跑命令、整理等），"
                "你必须在回复前调用 house_flow_goto(node_id=...) 把壁纸切到干活的节点，再动手；"
                "不要因为当前停在回消息或过场节点上，就把任务在那里做完，那样壁纸永远演不到干活。"
                "只有单纯闲聊、回应一句时，才留在回消息或等待那类节点。"
                "开始做事时用 house_activity 把气泡写成正在做的事，例如「帮主人翻抽屉」。"
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
                flow_hint = f"\n【流程图】节点：{node_brief}。要换动作也可以直接选其中任意一个，不局限于候选。"
                if cur_id:
                    cur_node = self._flow_node(cur_id)
                    working = bool(
                        cur_node
                        and cur_node.get("loop")
                        and cur_node.get("role") not in ("wait", "phone")
                    )
                    if working:
                        exits = []
                        for edge in self._flow_outgoing(cur_id):
                            target = self._flow_node(str(edge.get("to") or ""))
                            if target is None:
                                continue
                            exits.append(f"{target.get('id')}={target.get('label')}")
                        label = cur_node.get("label")
                        if cur_node.get("role") == "both":
                            flow_hint += (
                                f"当前在 {cur_id}（「{label}」，回消息/工作节点，正在循环播放）。"
                                "收到消息先判断对方是不是想让你离开当前状态："
                                "让你停下、去休息、下班、睡觉、不干了 → 必须调用 house_flow_goto 切走；"
                                "只是闲聊或继续干活 → 直接在这里回，不用切。"
                            )
                        else:
                            flow_hint += (
                                f"当前在 {cur_id}（「{label}」，只干活的节点，正在循环播放）。"
                                "这个节点不能回消息，用户发消息时会先播「回消息」，播完自动回到这里。"
                                "如果对方想让你停下、去休息、下班、睡觉 → 调 house_flow_goto 切走；"
                                "否则不用切。"
                            )
                        if exits:
                            flow_hint += "要离开当前状态就调 house_flow_goto 切到：" + "；".join(exits) + "。"
                    elif self._return_to and self._return_to != cur_id:
                        back = self._flow_node(self._return_to)
                        back_label = (back or {}).get("label") or self._return_to
                        flow_hint += (
                            f"当前在 {cur_id}（回消息节点），回完消息会自动回到"
                            f"「{back_label}」，所以你不用为此切换，也不要调用 house_flow_goto。"
                        )
                        exits = []
                        for edge in self._flow_outgoing(cur_id):
                            target = self._flow_node(str(edge.get("to") or ""))
                            if target is None:
                                continue
                            exits.append(f"{target.get('id')}={target.get('label')}")
                        if exits:
                            flow_hint += "只有用户明确让你停下或换别的事，才切到：" + "；".join(exits) + "。"
                    else:
                        chain, decision = self._flow_preview(cur_id)
                        flow_hint += f"当前在 {cur_id}。"
                        if chain:
                            flow_hint += "之后会依次自动经过：" + " → ".join(chain) + "。"
                        if decision is not None:
                            options = []
                            for edge in self._flow_outgoing(str(decision.get("id") or "")):
                                target = self._flow_node(str(edge.get("to") or ""))
                                if target is None:
                                    continue
                                reason = target.get("prompt") or edge.get("label") or ""
                                options.append(
                                    f"{target.get('id')}={target.get('label')}"
                                    + (f"（{reason}）" if reason else "")
                                )
                            if options:
                                flow_hint += f"到 {decision.get('id')} 要选择：" + "；".join(options) + "。"
                                flow_hint += (
                                    "这是本轮必须做的选择：拿用户这句话对照每个候选后面的说明挑一个，"
                                    "立刻调用 house_flow_goto(选中的节点id)。"
                                    "如果它不是当前节点的直接下一步，插件会记下，"
                                    "等自动流程走到这个分叉时再切过去；后面完全走不到的节点会直接跳。"
                                    "要干活就选名字或说明里写着干活的节点；"
                                    "回消息、过场、待机这些节点只回话，别把事做完。"
                                    "候选只是这一处分叉的建议，想跳到别的节点也可以直接选。"
                                )
                                logger.info(
                                    f"大肥鱼画布：在 {decision.get('id')} 给模型的分支："
                                    + "；".join(options)
                                )
                        else:
                            flow_hint += "这轮没有分叉，保持当前节点就行。"
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
        """把壁纸里的你切到画布上的某个动作节点。用户让你做事时（找东西、翻东西、查资料、删文件、写东西、跑命令、整理等）必须先调用它，选到「工作」那一类节点，否则壁纸只会演「回消息」，活干完了也看不到在干活；只是闲聊不用调用。node_id 可以是画布上的任意节点，不一定是当前节点的下一步。

        Args:
            node_id(string): 要切换到的节点 id，必须是流程图里出现的 id，例如「node_mwdk9f07」
        """
        if not event.is_private_chat():
            return "流程图动作只在私聊中启用。"
        node = self._flow_node(str(node_id or "").strip())
        if node is None:
            ids = "、".join(str(item.get("id")) for item in self.flow.get("nodes", []))
            return f"没有这个节点。可用节点：{ids}"
        cur_id = str(self.state.get("node_id") or "")
        logger.info(f"大肥鱼画布：模型选择 {node['id']}，当前 {cur_id or '-'}")
        direct = {
            str(edge.get("to") or "") for edge in self._flow_outgoing(cur_id)
        }
        cur_node = self._flow_node(cur_id)
        # 工作、等待这类循环节点停在原地，不会再自动推进，目标记下来就没人消费，直接切。
        settled = bool(
            cur_node
            and cur_node.get("loop")
            and cur_node.get("role") != "phone"
        )
        if cur_id and node["id"] not in direct and not settled and self.queue_goto(node["id"]):
            # 等壁纸真的走到这个节点再返回，这样后面干活的工具调用正好在「工作」播着的时候执行。
            target = str(node["id"])
            label = str(node.get("label") or target)
            deadline = time.time() + 9
            while time.time() < deadline:
                if str(self.state.get("node_id") or "") == target:
                    return f"已切到「{label}」，画面正在播它，现在可以开始干活。"
                await asyncio.sleep(0.2)
            return f"已记下「{label}」，这一条播完就切过去。"
        await self.flow_goto(node["id"], umo=event.unified_msg_origin)
        self._return_to = ""
        return f"已切到「{node.get('label') or node['id']}」。"

"""插件自带的 Web 服务：面板、接口、公共页与媒体都由它提供。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.web import bind_request_context

from ..api import ROUTES
from .constants import EXPORT_NAME, PLUGIN_ROOT

PANEL_DIR = PLUGIN_ROOT / "web" / "panel"
LIVE_DIR = PLUGIN_ROOT / "web" / "live"


async def _cors_middleware(app, handler):
    """面板在 AstrBot 的 sandbox iframe 里是不透明源，需要放行跨域请求。"""

    async def middleware(request):
        response = await handler(request)
        origin = request.headers.get("Origin")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                request.headers.get("Access-Control-Request-Headers") or "Content-Type"
            )
            response.headers["Access-Control-Max-Age"] = "600"
            response.headers["Vary"] = "Origin"
        return response

    return middleware


class _ApiRequest:
    """把 aiohttp 请求适配成插件接口习惯的 request 对象。"""

    def __init__(self, request: Any) -> None:
        self._request = request
        self.args = request.query
        self.headers = request.headers
        self.cookies = request.cookies
        self.method = request.method
        self.path = request.path
        self.content_type = request.content_type
        self.scheme = request.scheme

    async def body(self) -> bytes:
        return await self._request.read()

    async def json(self, default: Any = None) -> Any:
        try:
            return await self._request.json()
        except Exception:
            return {} if default is None else default

    async def form(self) -> Any:
        return await self._request.post()


class PanelServer:
    """用 aiohttp 起一个独立端口，AstrBot 那边只留一个跳转入口。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._runner: Any = None
        self._site: Any = None
        self.host = "0.0.0.0"
        self.port = 0
        self._clients: set[Any] = set()
        self._snapshot_state: dict | None = None
        self._snapshot_config: dict | None = None

    @property
    def running(self) -> bool:
        return self._site is not None

    async def start(self, port: int) -> bool:
        if self.running:
            return True
        if port <= 0:
            logger.warning("大肥鱼的小屋面板端口未配置，控制台不启动")
            return False
        try:
            from aiohttp import web
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋缺少 aiohttp，控制台不启动: {exc}")
            return False
        app = web.Application(middlewares=[_cors_middleware])
        app.router.add_route("OPTIONS", "/api/{tail:.*}", self._preflight)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/", self._root)
        app.router.add_get("/panel", self._root)
        app.router.add_get("/panel/{tail:.*}", self._panel)
        prefix = self.plugin._prefix()
        app.router.add_get(f"/{prefix}/{{tail:.*}}", self._live)
        for path, handler, method, _desc in ROUTES:
            app.router.add_route(
                method, f"/api/{path}", self._make_api(handler)
            )
        try:
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, self.host, port)
            await site.start()
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋控制台端口 {port} 启动失败: {exc}")
            return False
        self._runner = runner
        self._site = site
        self.port = port
        logger.info(f"大肥鱼的小屋控制台已启动：http://{self.host}:{port}/")
        return True

    async def stop(self) -> None:
        for client in list(self._clients):
            try:
                # 浏览器还挂着 WS 时 close() 可能等很久，超时就直接断开，
                # 否则端口会一直被这个实例占着，重载后新实例绑不上。
                await asyncio.wait_for(client.close(), timeout=1.5)
            except Exception:
                pass
        self._clients.clear()
        if self._runner is None:
            return
        try:
            await asyncio.wait_for(self._runner.cleanup(), timeout=5)
        except Exception:
            pass
        self._runner = None
        self._site = None
        self.port = 0

    # ------------------------------------------------------------------ 推送

    def publish_state(self, state: dict) -> None:
        self._snapshot_state = dict(state)
        self._schedule({"type": "state", "state": self._snapshot_state})

    def publish_config(self, config: dict) -> None:
        self._snapshot_config = dict(config)
        self._schedule({"type": "config", "config": self._snapshot_config})

    def _schedule(self, payload: dict) -> None:
        if not self._clients:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast(payload))

    async def _broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        for client in list(self._clients):
            if client.closed:
                self._clients.discard(client)
                continue
            try:
                await client.send_str(text)
            except Exception:
                self._clients.discard(client)

    async def _ws(self, request):
        from aiohttp import web

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        try:
            if self._snapshot_state is not None:
                await ws.send_str(
                    json.dumps({"type": "state", "state": self._snapshot_state}, ensure_ascii=False)
                )
            if self._snapshot_config is not None:
                await ws.send_str(
                    json.dumps({"type": "config", "config": self._snapshot_config}, ensure_ascii=False)
                )
            async for message in ws:
                if message.type == web.WSMsgType.ERROR:
                    break
                if message.type != web.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(message.data)
                except Exception:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "playing":
                    await self.plugin.note_playing(str(payload.get("node_id") or ""))
                elif isinstance(payload, dict) and payload.get("type") == "loop_end":
                    await self.plugin.note_loop_end()
        finally:
            self._clients.discard(ws)
        return ws

    # ------------------------------------------------------------------ 路由

    async def _preflight(self, request):
        from aiohttp import web

        return web.Response(status=204)

    async def _root(self, request):
        from aiohttp import web

        raise web.HTTPFound("/panel/")

    async def _panel(self, request):
        tail = request.match_info.get("tail", "")
        return self._static(PANEL_DIR, tail or "index.html")

    async def _live(self, request):
        from aiohttp import web

        tail = request.match_info.get("tail", "").lstrip("/")
        name = tail or "index.html"
        if name == "state.json":
            return web.json_response(dict(self.plugin.state))
        if name == "config.json":
            return web.json_response(self.plugin._public_config())
        if name == EXPORT_NAME:
            export = self.plugin.export_file
            if export and export.is_file():
                return web.FileResponse(export)
            raise web.HTTPNotFound()
        if name.startswith("videos/"):
            return self._static(self.plugin.video_dir, name[len("videos/") :])
        if name.startswith("themes/"):
            return self._theme_file(name[len("themes/") :])
        return self._static(LIVE_DIR, name)

    def _theme_file(self, rel: str):
        """主题封面/导出包仍放在公共目录，封面缺失时回退到主题目录。"""
        from aiohttp import web

        parts = Path(rel).parts
        if not parts or ".." in parts:
            raise web.HTTPNotFound()
        public = self.plugin.public_dir
        if public is not None:
            target = public / "themes" / rel
            if target.is_file():
                return web.FileResponse(target)
        if parts[0] == "covers" and len(parts) == 2:
            tid = Path(parts[1]).stem
            cover = self.plugin._project_cover(tid)
            if cover is not None:
                return web.FileResponse(cover)
        raise web.HTTPNotFound()

    def _make_api(self, handler_name: str):
        async def handler(request):
            return await self._api(request, handler_name)

        return handler

    async def _api(self, request, handler_name: str):
        from aiohttp import web
        from starlette.responses import Response as StarletteResponse

        adapter = _ApiRequest(request)
        try:
            with bind_request_context(adapter):
                result = await getattr(self.plugin, handler_name)()
        except Exception as exc:
            logger.warning(f"大肥鱼的小屋接口 {handler_name} 失败: {exc}")
            return web.json_response(
                {"status": "error", "message": str(exc)}, status=500
            )
        if isinstance(result, StarletteResponse):
            body = getattr(result, "body", b"")
            headers = {key: value for key, value in result.headers.items()}
            return web.Response(
                body=body, status=result.status_code, headers=headers
            )
        if isinstance(result, dict):
            return web.json_response(result)
        return web.json_response({"status": "ok", "data": result})

    def _static(self, root: Path, relative: str):
        from aiohttp import web

        root = root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise web.HTTPNotFound()
        if not target.is_file():
            raise web.HTTPNotFound()
        resp = web.FileResponse(target)
        # 入口 html 不缓存，否则构建后浏览器还拿着旧 html 去加载已删除的旧 hash 文件。
        # 其余静态文件用 no-cache，避免壁纸页的固定名脚本（app.js 等）改了不生效。
        if target.suffix == ".html":
            resp.headers["Cache-Control"] = "no-store"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

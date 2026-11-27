"""FastAPI application: health check, live stream, MCP endpoint and dashboard."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from backend.auth import Authenticator, BearerAuthMiddleware, unauthorized_response
from backend.config import Settings
from backend.service import EpisodeService
from mcp_server.server import build_mcp_server

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
KEEPALIVE_SECONDS = 15


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def event_stream(service: EpisodeService, agent: str) -> AsyncIterator[str]:
    """A snapshot of the agent's state followed by one event per tool call."""
    with service.subscribe(agent) as queue:
        # No await between subscribing and the snapshot, so no event is lost.
        yield _sse(service.snapshot(agent))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                return
            yield _sse(event)


class DashboardFiles(StaticFiles):
    """Static files the browser must revalidate, so a rebuild shows up on reload."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    settings: Settings,
    service: EpisodeService | None = None,
    frontend_dist: Path = FRONTEND_DIST,
) -> FastAPI:
    service = service or EpisodeService()
    authenticator = Authenticator(settings.api_keys)

    mcp_server = build_mcp_server(service)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        # No MCP sessions: all state lives in episodes, so clients survive
        # reconnects and server restarts without a stale session ID.
        stateless_http=True,
        # Host-header checks would reject remote hostnames. Every request needs
        # a bearer token, which a DNS-rebinding web page does not have.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(title="RouteSim", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/stream")
    async def stream(request: Request):
        agent = authenticator.authenticate(request.headers.get("authorization"))
        if agent is None:
            return unauthorized_response()
        return StreamingResponse(
            event_stream(service, agent),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    for route in mcp_app.routes:
        app.router.routes.append(
            Route(route.path, endpoint=BearerAuthMiddleware(route.endpoint, authenticator))
        )

    # Mounted last so it cannot shadow the routes above.
    if frontend_dist.is_dir():
        app.mount("/", DashboardFiles(directory=frontend_dist, html=True), name="frontend")

    return app

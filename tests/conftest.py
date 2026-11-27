import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from backend.app import create_app
from backend.config import Settings
from backend.service import EpisodeService

ALICE = "alice-test-token-0123456789"
BOB = "bob-test-token-0123456789abc"
API_KEYS = {"alice": ALICE, "bob": BOB}

# Shortest route through the default layout: 12 moves, no hazards or obstacles.
WINNING_PATH = ["right"] * 5 + ["down"] * 4 + ["right"] + ["down"] * 2


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class LiveServer:
    url: str
    service: EpisodeService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def live_server(tmp_path: Path):
    """The real application served by uvicorn on a free local port."""
    service = EpisodeService()
    app = create_app(
        Settings(api_keys=API_KEYS), service=service, frontend_dist=tmp_path / "no-frontend"
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=1
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Test server did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    yield LiveServer(f"http://127.0.0.1:{port}", service)

    server.should_exit = True
    thread.join(timeout=10)


@asynccontextmanager
async def mcp_client(base_url: str, token: str, mode: str = "auto") -> AsyncIterator[Client]:
    """The official MCP client connected over Streamable HTTP."""
    async with httpx2.AsyncClient(headers=bearer(token), timeout=10) as http:
        transport = streamable_http_client(f"{base_url}/mcp", http_client=http)
        async with Client(transport, mode=mode) as client:
            yield client


async def call(client: Client, tool: str, **arguments) -> dict:
    """Call a tool that is expected to succeed and return its structured result."""
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def call_error(client: Client, tool: str, **arguments) -> str:
    """Call a tool that is expected to fail and return the error text."""
    result = await client.call_tool(tool, arguments)
    assert result.is_error
    return result.content[0].text


async def sse_events(response: httpx2.Response) -> AsyncIterator[dict]:
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            yield json.loads(line.removeprefix("data: "))

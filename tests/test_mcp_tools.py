"""Every MCP tool, called by the official client over real Streamable HTTP."""

import httpx2
import pytest

from conftest import ALICE, BOB, SHORTEST_ROUTE, call, call_error, mcp_client

pytestmark = pytest.mark.anyio

TOOLS = {
    "start_episode": set(),
    "reset_episode": {"episode_id"},
    "get_observation": {"episode_id"},
    "inspect_environment": {"episode_id"},
    "move_agent": {"episode_id", "direction"},
    "get_episode_status": {"episode_id"},
}


# "legacy" is the initialize handshake; "auto" negotiates the newest protocol.
@pytest.fixture(params=["auto", "legacy"])
async def client(live_server, request):
    async with mcp_client(live_server.url, ALICE, request.param) as connected:
        yield connected


async def test_exposes_exactly_the_expected_tools(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == set(TOOLS)
    for name, parameters in TOOLS.items():
        schema = tools[name].input_schema
        # The injected request context must not leak into the public schema.
        assert set(schema.get("properties", {})) == parameters
        assert set(schema.get("required", [])) == parameters
        assert tools[name].description
    direction = tools["move_agent"].input_schema["properties"]["direction"]
    assert direction["enum"] == ["up", "down", "left", "right"]


async def test_start_episode(client, live_server):
    observation = await call(client, "start_episode")
    assert observation["status"] == "in_progress"
    assert observation["position"] == {"row": 0, "col": 0}
    assert observation["goal"] == {"row": 6, "col": 6}
    # The tool created the episode in the backend's own storage.
    [stored] = live_server.service.snapshot("alice")["episodes"]
    assert stored["episode_id"] == observation["episode_id"]


async def test_get_observation(client):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    observation = await call(client, "get_observation", episode_id=episode_id)
    assert observation["valid_actions"] == ["down", "right"]
    assert observation["neighbors"]["up"] == "wall"
    assert observation["steps_remaining"] == 50
    assert observation["total_coins"] == 5
    assert observation["coins_collected"] == 0
    assert {"row": 2, "col": 1} in observation["available_coins"]


async def test_inspect_environment(client):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    inspection = await call(client, "inspect_environment", episode_id=episode_id)
    assert inspection["grid_size"] == 7
    assert len(inspection["map"]) == 7
    assert {"row": 1, "col": 1} in inspection["obstacles"]
    assert {"row": 1, "col": 4} in inspection["hazards"]
    assert inspection["rewards"] == {
        "step": -1,
        "collision": -1,
        "coin": 5,
        "goal": 20,
        "hazard": -20,
    }
    assert len(inspection["available_coins"]) == 5
    assert "".join(inspection["map"]).count("C") == 5


@pytest.mark.parametrize(
    ("moves", "position"),
    [
        (["down"], {"row": 1, "col": 0}),
        (["down", "up"], {"row": 0, "col": 0}),
        (["right"], {"row": 0, "col": 1}),
        (["right", "left"], {"row": 0, "col": 0}),
    ],
)
async def test_move_agent_in_each_direction(client, moves, position):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    for direction in moves:
        result = await call(client, "move_agent", episode_id=episode_id, direction=direction)
    assert result["position"] == position
    assert result["outcome"] == "moved"
    assert result["reward"] == -1
    assert result["cumulative_reward"] == -len(moves)


async def test_get_episode_status_returns_results(client):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    for direction in SHORTEST_ROUTE:
        await call(client, "move_agent", episode_id=episode_id, direction=direction)
    results = await call(client, "get_episode_status", episode_id=episode_id)
    assert results["status"] == "succeeded"
    assert results["cumulative_reward"] == 8
    assert results["coins_collected"] == 0
    assert len(results["trajectory"]) == 12


async def test_finished_episode_rejects_moves_until_reset(client):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    for direction in ["right"] * 4 + ["down"]:
        result = await call(client, "move_agent", episode_id=episode_id, direction=direction)
    assert result["status"] == "failed"

    error = await call_error(client, "move_agent", episode_id=episode_id, direction="left")
    assert "already ended" in error

    reset = await call(client, "reset_episode", episode_id=episode_id)
    assert reset["episode_id"] == episode_id
    assert reset["status"] == "in_progress"
    assert (reset["step_count"], reset["cumulative_reward"]) == (0, 0)


async def test_invalid_direction_is_a_logged_tool_error(client, live_server):
    episode_id = (await call(client, "start_episode"))["episode_id"]
    error = await call_error(client, "move_agent", episode_id=episode_id, direction="north")
    assert "up, down, left, right" in error
    assert live_server.service.snapshot("alice")["log"][-1]["ok"] is False


async def test_unknown_episode_is_a_tool_error(client):
    error = await call_error(client, "get_observation", episode_id="ep_missing")
    assert "not found" in error


async def test_agents_cannot_touch_each_others_episodes(live_server):
    async with mcp_client(live_server.url, ALICE) as alice, mcp_client(live_server.url, BOB) as bob:
        alice_id = (await call(alice, "start_episode"))["episode_id"]
        bob_id = (await call(bob, "start_episode"))["episode_id"]

        for tool in ["reset_episode", "get_observation", "inspect_environment", "get_episode_status"]:
            assert "not found" in await call_error(bob, tool, episode_id=alice_id)
        assert "not found" in await call_error(
            bob, "move_agent", episode_id=alice_id, direction="right"
        )

        await call(bob, "move_agent", episode_id=bob_id, direction="down")
        observation = await call(alice, "get_observation", episode_id=alice_id)
        assert observation["step_count"] == 0


async def test_episode_survives_a_client_reconnect(live_server):
    async with mcp_client(live_server.url, ALICE) as first:
        episode_id = (await call(first, "start_episode"))["episode_id"]
        await call(first, "move_agent", episode_id=episode_id, direction="right")
    async with mcp_client(live_server.url, ALICE) as second:
        observation = await call(second, "get_observation", episode_id=episode_id)
    assert observation["step_count"] == 1


async def test_non_local_host_header_is_accepted(live_server):
    """A deployed server is reached by its public hostname, not localhost."""
    async with httpx2.AsyncClient(base_url=live_server.url) as http:
        response = await http.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {ALICE}",
                "Host": "routesim.example.com",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
    assert response.status_code == 200
    assert "routesim" in response.text

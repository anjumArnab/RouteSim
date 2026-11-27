"""A complete agent session watched through the dashboard's live stream.

The scripted agent uses only what the MCP tools return: it reads the map from
inspect_environment, plans a route, moves, and reads the results.
"""

import asyncio
from collections import deque

import httpx2
import pytest

from conftest import ALICE, bearer, call, call_error, mcp_client, sse_events

pytestmark = pytest.mark.anyio

STEPS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}


def plan_route(inspection: dict) -> list[str]:
    """Breadth-first search over the map the tool returned."""
    grid = inspection["map"]
    start = (inspection["start"]["row"], inspection["start"]["col"])
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        (row, col), route = queue.popleft()
        if grid[row][col] == "G":
            return route
        for direction, (d_row, d_col) in STEPS.items():
            r, c = row + d_row, col + d_col
            if 0 <= r < 7 and 0 <= c < 7 and (r, c) not in seen and grid[r][c] not in "#H":
                seen.add((r, c))
                queue.append(((r, c), [*route, direction]))
    raise AssertionError("No route to the goal")


async def test_agent_fails_resets_and_reaches_the_goal(live_server):
    async with httpx2.AsyncClient(base_url=live_server.url, timeout=10) as http:
        async with http.stream("GET", "/api/stream", headers=bearer(ALICE)) as response:
            events = sse_events(response)
            snapshot = await anext(events)
            assert snapshot["episodes"] == [] and snapshot["log"] == []

            received = []
            async with mcp_client(live_server.url, ALICE) as agent:

                async def step(tool: str, **arguments) -> dict:
                    """Call a tool and wait for the dashboard to hear about it."""
                    result = await call(agent, tool, **arguments)
                    event = await asyncio.wait_for(anext(events), timeout=5)
                    assert event["entry"]["tool"] == tool
                    assert event["entry"]["arguments"] == arguments
                    assert event["entry"]["result"] == result
                    received.append(event)
                    return result

                episode_id = (await step("start_episode"))["episode_id"]
                inspection = await step("inspect_environment", episode_id=episode_id)
                await step("get_observation", episode_id=episode_id)

                # First attempt: bump into the wall, then walk into a hazard.
                blocked = await step("move_agent", episode_id=episode_id, direction="up")
                assert blocked["outcome"] == "blocked"
                for direction in ["right"] * 4 + ["down"]:
                    last = await step("move_agent", episode_id=episode_id, direction=direction)
                assert last["status"] == "failed"
                assert last["cumulative_reward"] == -26

                await call_error(agent, "move_agent", episode_id=episode_id, direction="down")
                rejected = await asyncio.wait_for(anext(events), timeout=5)
                assert rejected["entry"]["ok"] is False
                assert rejected["episode"]["status"] == "failed"

                # Second attempt: follow a planned route.
                await step("reset_episode", episode_id=episode_id)
                route = plan_route(inspection)
                for direction in route:
                    last = await step("move_agent", episode_id=episode_id, direction=direction)
                assert last["done"] and last["status"] == "succeeded"

                results = await step("get_episode_status", episode_id=episode_id)

    assert results["reached_goal"]
    assert results["step_count"] == len(route) == 12
    assert results["cumulative_reward"] == 9

    # What the dashboard was told matches what the agent saw.
    final = received[-1]["episode"]
    for key in ("episode_id", "position", "step_count", "cumulative_reward", "status"):
        assert final[key] == results[key]
    after_reset = received[-len(route) - 1 : -1]
    assert sum(e["entry"]["reward"] for e in after_reset) == results["cumulative_reward"]

    # A dashboard that connects later receives the same history.
    replay = live_server.service.snapshot("alice")
    assert [e["seq"] for e in replay["log"]] == sorted(e["seq"] for e in replay["log"])
    assert len(replay["log"]) == len(received) + 1
    assert replay["episodes"] == [final]

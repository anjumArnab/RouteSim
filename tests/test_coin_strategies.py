"""Three ways to cross the grid, and proof of which one pays best.

shortest  12 moves, 0 coins: 20 - 12      =  8
all coins 30 moves, 5 coins: 20 + 25 - 30 = 15
balanced  18 moves, 4 coins: 20 + 20 - 18 = 22
"""

import copy
from collections import deque
from dataclasses import dataclass

import pytest

from backend.environment import DEFAULT_LAYOUT, Action, GridWorld, Status
from conftest import (
    ALICE,
    ALL_COINS_ROUTE,
    BALANCED_ROUTE,
    SHORTEST_ROUTE,
    call,
    call_error,
    mcp_client,
)

FAR_COIN = (6, 0)  # costs 12 extra moves for a reward of 5


@dataclass(frozen=True)
class Strategy:
    route: list[str]
    moves: int
    coins: list[tuple[int, int]]  # in the order collected
    reward: int


STRATEGIES = {
    "shortest": Strategy(SHORTEST_ROUTE, 12, [], 8),
    "all coins": Strategy(ALL_COINS_ROUTE, 30, [FAR_COIN, (2, 1), (1, 3), (0, 6), (2, 5)], 15),
    "balanced": Strategy(BALANCED_ROUTE, 18, [(2, 1), (1, 3), (0, 6), (2, 5)], 22),
}


@pytest.fixture(params=STRATEGIES.values(), ids=STRATEGIES.keys())
def strategy(request) -> Strategy:
    return request.param


def test_state_transitions_rewards_and_termination(strategy):
    world = GridWorld()
    expected_total = 0
    for number, direction in enumerate(strategy.route, start=1):
        assert world.status is Status.IN_PROGRESS
        available_before = set(world.available_coins())
        target = world.target(Action(direction))

        result = world.step(Action(direction))

        picked_up = target in available_before
        reached_goal = target == DEFAULT_LAYOUT.goal
        assert world.position == target
        assert result.reward == -1 + (5 if picked_up else 0) + (20 if reached_goal else 0)
        assert result.coin_collected == (target if picked_up else None)
        assert set(world.available_coins()) == available_before - {target}
        expected_total += result.reward
        assert world.cumulative_reward == expected_total
        assert world.step_count == number

    assert world.status is Status.SUCCEEDED
    assert world.step_count == strategy.moves
    assert world.collected_coins == strategy.coins
    assert world.cumulative_reward == strategy.reward


def best_finish_per_coin_set() -> dict[frozenset, tuple[int, int]]:
    """Search every reachable (position, coins held) state of the real environment.

    Returns the fewest-move successful finish for each set of coins as
    {coins: (cumulative reward, moves)}.
    """
    start = GridWorld()
    queue = deque([start])
    seen = {(start.position, frozenset())}
    finishes: dict[frozenset, tuple[int, int]] = {}
    while queue:  # breadth-first, so the first arrival uses the fewest moves
        world = queue.popleft()
        for action in world.valid_actions():
            child = copy.deepcopy(world)
            child.step(action)
            coins = frozenset(child.collected_coins)
            if child.status is Status.SUCCEEDED:
                finishes.setdefault(coins, (child.cumulative_reward, child.step_count))
            elif child.status is Status.IN_PROGRESS and (child.position, coins) not in seen:
                seen.add((child.position, coins))
                queue.append(child)
    return finishes


def test_balancing_beats_both_the_shortest_route_and_collecting_everything():
    finishes = best_finish_per_coin_set()
    shortest, greedy, balanced = (STRATEGIES[n] for n in ("shortest", "all coins", "balanced"))

    # Each scripted route is the most efficient way to hold exactly its coins.
    for strategy in (shortest, greedy, balanced):
        assert finishes[frozenset(strategy.coins)] == (strategy.reward, strategy.moves)

    # The balanced route is the single best outcome the environment allows.
    best_coins = max(finishes, key=lambda coins: finishes[coins][0])
    assert best_coins == frozenset(balanced.coins)
    assert [r for r, _ in finishes.values()].count(balanced.reward) == 1
    assert shortest.reward < greedy.reward < balanced.reward

    # A shortest route cannot sweep up more than one coin on the way.
    assert min(moves for _, moves in finishes.values()) == shortest.moves
    assert all(len(coins) <= 1 for coins, (_, m) in finishes.items() if m == shortest.moves)


@pytest.mark.anyio
async def test_strategy_through_mcp(live_server, strategy):
    """An external agent sees coins appear and vanish using only the existing tools."""
    coin = lambda position: {"row": position[0], "col": position[1]}
    async with mcp_client(live_server.url, ALICE) as agent:
        start = await call(agent, "start_episode")
        episode_id = start["episode_id"]
        assert sorted(start["available_coins"], key=str) == sorted(
            map(coin, DEFAULT_LAYOUT.coins), key=str
        )

        remaining = start["available_coins"]
        total = 0
        for direction in strategy.route:
            result = await call(agent, "move_agent", episode_id=episode_id, direction=direction)
            total += result["reward"]
            assert result["reward"] == sum(result["reward_breakdown"].values())
            assert result["cumulative_reward"] == total
            if result["coin_collected"]:
                assert result["outcome"] == "coin"
                assert result["coin_collected"] == result["position"]
                remaining = [c for c in remaining if c != result["position"]]
            observation = await call(agent, "get_observation", episode_id=episode_id)
            assert observation["available_coins"] == remaining

        results = await call(agent, "get_episode_status", episode_id=episode_id)
        error = await call_error(agent, "move_agent", episode_id=episode_id, direction="up")

    assert "already ended" in error
    assert results["status"] == "succeeded"
    assert results["step_count"] == strategy.moves
    assert results["coins_collected"] == len(strategy.coins)
    assert results["collected_coin_positions"] == [coin(c) for c in strategy.coins]
    assert results["cumulative_reward"] == strategy.reward

    log = live_server.service.snapshot("alice")["log"]
    pickups = [entry["coin_collected"] for entry in log if entry["coin_collected"]]
    assert pickups == [coin(c) for c in strategy.coins]
    assert sum(entry["reward"] or 0 for entry in log) == strategy.reward

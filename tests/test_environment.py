from collections import deque

import pytest

from backend.environment import (
    DEFAULT_LAYOUT,
    MAX_STEPS,
    Action,
    EpisodeFinishedError,
    GridWorld,
    Layout,
    Outcome,
    Status,
)
from conftest import SHORTEST_ROUTE


def run(world: GridWorld, moves: list[str]):
    return [world.step(Action(move)) for move in moves]


def test_default_layout_is_a_7x7_grid_with_separate_special_cells():
    layout = DEFAULT_LAYOUT
    assert layout.size == 7
    assert layout.start == (0, 0)
    assert layout.goal == (6, 6)
    assert layout.obstacles and layout.hazards
    assert not layout.obstacles & layout.hazards
    assert not {layout.start, layout.goal} & (layout.obstacles | layout.hazards)
    assert len(layout.coins) == 5
    assert not layout.coins & (layout.obstacles | layout.hazards | {layout.start, layout.goal})


def test_goal_is_reachable_without_touching_hazards():
    layout = DEFAULT_LAYOUT
    distance = {layout.start: 0}
    queue = deque([layout.start])
    while queue:
        row, col = queue.popleft()
        for nxt in [(row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)]:
            if nxt not in distance and layout.cell_type(nxt) in ("free", "goal"):
                distance[nxt] = distance[(row, col)] + 1
                queue.append(nxt)
    assert distance[layout.goal] == len(SHORTEST_ROUTE)


@pytest.mark.parametrize(
    "rows",
    [
        ["S.....G"],  # wrong height
        ["S.....G"] + ["......"] * 6,  # wrong width
        ["......."] * 7,  # no start or goal
        ["S.....G"] + ["......."] * 5 + ["......S"],  # two starts
        ["S.....G"] + ["......."] * 5 + ["......?"],  # unknown character
    ],
)
def test_invalid_maps_are_rejected(rows):
    with pytest.raises(ValueError):
        Layout.from_map(rows)


def test_initial_state():
    world = GridWorld()
    assert world.position == (0, 0)
    assert world.step_count == 0
    assert world.cumulative_reward == 0
    assert world.status is Status.IN_PROGRESS
    assert not world.done
    assert world.valid_actions() == [Action.DOWN, Action.RIGHT]
    assert world.neighbors() == {"up": "wall", "down": "free", "left": "wall", "right": "free"}


@pytest.mark.parametrize(
    ("origin", "direction", "expected"),
    [
        ((2, 0), "up", (1, 0)),
        ((2, 0), "down", (3, 0)),
        ((2, 3), "left", (2, 2)),
        ((2, 2), "right", (2, 3)),
    ],
)
def test_each_direction_moves_one_cell(origin, direction, expected):
    world = GridWorld()
    world.position = origin
    result = world.step(Action(direction))
    assert result.outcome is Outcome.MOVED
    assert world.position == expected
    assert result.reward == -1


def test_wall_blocks_movement_and_is_penalised():
    world = GridWorld()
    result = world.step(Action.UP)
    assert result.outcome is Outcome.BLOCKED
    assert world.position == (0, 0)
    assert (result.reward, world.step_count, world.cumulative_reward) == (-2, 1, -2)
    assert not world.done


def test_obstacle_blocks_movement():
    world = GridWorld()
    run(world, ["right"])
    result = world.step(Action.DOWN)  # (1, 1) is an obstacle
    assert result.outcome is Outcome.BLOCKED
    assert world.position == (0, 1)
    assert Action.DOWN not in world.valid_actions()


def test_hazard_fails_the_episode():
    world = GridWorld()
    run(world, ["right"] * 4)
    assert world.neighbors()["down"] == "hazard"
    assert Action.DOWN in world.valid_actions()  # legal, but dangerous
    result = world.step(Action.DOWN)
    assert result.outcome is Outcome.HAZARD
    assert result.breakdown == {"step": -1, "hazard": -20}
    assert result.reward == -21
    assert world.position == (1, 4)
    assert world.status is Status.FAILED
    assert world.cumulative_reward == -25
    assert world.valid_actions() == []


def test_reaching_the_goal_succeeds():
    world = GridWorld()
    results = run(world, SHORTEST_ROUTE)
    assert [r.reward for r in results] == [-1] * 11 + [19]
    assert results[-1].breakdown == {"step": -1, "goal": 20}
    assert results[-1].outcome is Outcome.GOAL
    assert world.status is Status.SUCCEEDED
    assert world.position == DEFAULT_LAYOUT.goal
    assert world.step_count == 12
    assert world.cumulative_reward == 8


def test_no_moves_after_the_episode_ends():
    world = GridWorld()
    run(world, SHORTEST_ROUTE)
    before = (world.position, world.step_count, world.cumulative_reward)
    with pytest.raises(EpisodeFinishedError):
        world.step(Action.LEFT)
    assert (world.position, world.step_count, world.cumulative_reward) == before


def test_episode_is_truncated_at_the_step_limit():
    world = GridWorld()
    run(world, ["up"] * (MAX_STEPS - 1))
    assert world.status is Status.IN_PROGRESS
    world.step(Action.UP)
    assert world.status is Status.TRUNCATED
    assert world.step_count == MAX_STEPS
    with pytest.raises(EpisodeFinishedError):
        world.step(Action.RIGHT)


def test_goal_on_the_last_step_counts_as_success():
    world = GridWorld(max_steps=len(SHORTEST_ROUTE))
    run(world, SHORTEST_ROUTE)
    assert world.status is Status.SUCCEEDED


# ----- coins ------------------------------------------------------------------

# From the start, "down, down, right" ends on the coin at (2, 1).
TO_FIRST_COIN = ["down", "down", "right"]
FIRST_COIN = (2, 1)


def test_collecting_a_coin_rewards_and_removes_it():
    world = GridWorld()
    run(world, TO_FIRST_COIN[:-1])
    assert world.neighbors()["right"] == "coin"
    assert FIRST_COIN in world.available_coins()

    result = world.step(Action.RIGHT)

    assert result.outcome is Outcome.COIN
    assert result.coin_collected == FIRST_COIN
    assert result.breakdown == {"step": -1, "coin": 5}
    assert result.reward == 4
    assert world.cumulative_reward == -1 - 1 + 4
    assert world.collected_coins == [FIRST_COIN]
    assert FIRST_COIN not in world.available_coins()
    assert len(world.available_coins()) == 4
    assert world.status is Status.IN_PROGRESS


def test_a_coin_never_rewards_twice():
    world = GridWorld()
    run(world, TO_FIRST_COIN)
    world.step(Action.LEFT)
    assert world.neighbors()["right"] == "free"
    again = world.step(Action.RIGHT)
    assert again.outcome is Outcome.MOVED
    assert again.coin_collected is None
    assert again.breakdown == {"step": -1}
    assert world.collected_coins == [FIRST_COIN]


def test_blocked_moves_next_to_a_coin_collect_nothing():
    world = GridWorld()
    run(world, ["right", "right"])  # (0, 2), above an obstacle
    result = world.step(Action.DOWN)
    assert result.breakdown == {"step": -1, "collision": -1}
    assert world.collected_coins == []


def test_collected_coins_still_count_when_the_episode_fails():
    world = GridWorld()
    results = run(world, [*TO_FIRST_COIN, "down"])  # (3, 1) is a hazard
    assert world.status is Status.FAILED
    assert [r.reward for r in results] == [-1, -1, 4, -21]
    assert world.cumulative_reward == -19
    assert world.collected_coins == [FIRST_COIN]


def test_coin_on_the_last_step_counts_before_truncation():
    world = GridWorld(max_steps=len(TO_FIRST_COIN))
    run(world, TO_FIRST_COIN)
    assert world.status is Status.TRUNCATED
    assert world.cumulative_reward == 2
    assert world.collected_coins == [FIRST_COIN]


def test_cumulative_reward_is_the_sum_of_every_breakdown():
    world = GridWorld()
    results = run(world, ["up", *TO_FIRST_COIN, "up", "down"])
    assert world.cumulative_reward == sum(sum(r.breakdown.values()) for r in results)


def test_reset_restores_collected_coins():
    world = GridWorld()
    run(world, TO_FIRST_COIN)
    world.reset()
    assert world.collected_coins == []
    assert set(world.available_coins()) == world.layout.coins
    assert run(world, TO_FIRST_COIN)[-1].reward == 4


def test_episodes_do_not_share_coins():
    first, second = GridWorld(), GridWorld()
    run(first, TO_FIRST_COIN)
    assert FIRST_COIN in second.available_coins()


def test_path_follows_the_agent_and_skips_blocked_moves():
    world = GridWorld()
    assert world.path() == [(0, 0)]
    run(world, ["up", "right", "down", "left"])  # "up" and "down" are blocked
    assert world.path() == [(0, 0), (0, 1), (0, 0)]
    world.reset()
    assert world.path() == [(0, 0)]


def test_reset_restores_the_initial_state():
    world = GridWorld()
    run(world, SHORTEST_ROUTE)
    world.reset()
    assert world.position == DEFAULT_LAYOUT.start
    assert (world.step_count, world.cumulative_reward) == (0, 0)
    assert world.status is Status.IN_PROGRESS
    assert world.history == []

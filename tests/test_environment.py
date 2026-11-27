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
from conftest import WINNING_PATH


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
    assert distance[layout.goal] == len(WINNING_PATH)


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
        ((2, 1), "left", (2, 0)),
        ((2, 0), "right", (2, 1)),
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
    assert result.reward == -20
    assert world.position == (1, 4)
    assert world.status is Status.FAILED
    assert world.cumulative_reward == -24
    assert world.valid_actions() == []


def test_reaching_the_goal_succeeds():
    world = GridWorld()
    results = run(world, WINNING_PATH)
    assert [r.reward for r in results] == [-1] * 11 + [20]
    assert results[-1].outcome is Outcome.GOAL
    assert world.status is Status.SUCCEEDED
    assert world.position == DEFAULT_LAYOUT.goal
    assert world.step_count == 12
    assert world.cumulative_reward == 9


def test_no_moves_after_the_episode_ends():
    world = GridWorld()
    run(world, WINNING_PATH)
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
    world = GridWorld(max_steps=len(WINNING_PATH))
    run(world, WINNING_PATH)
    assert world.status is Status.SUCCEEDED


def test_path_follows_the_agent_and_skips_blocked_moves():
    world = GridWorld()
    assert world.path() == [(0, 0)]
    run(world, ["up", "right", "down", "left"])  # "up" and "down" are blocked
    assert world.path() == [(0, 0), (0, 1), (0, 0)]
    world.reset()
    assert world.path() == [(0, 0)]


def test_reset_restores_the_initial_state():
    world = GridWorld()
    run(world, WINNING_PATH)
    world.reset()
    assert world.position == DEFAULT_LAYOUT.start
    assert (world.step_count, world.cumulative_reward) == (0, 0)
    assert world.status is Status.IN_PROGRESS
    assert world.history == []

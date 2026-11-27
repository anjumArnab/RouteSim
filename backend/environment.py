"""The 7x7 grid navigation environment.

This module is the only place where state transitions and rewards are computed.
Positions are (row, col) pairs with row 0 at the top and col 0 at the left.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

GRID_SIZE = 7
MAX_STEPS = 50


class Action(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class Status(StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"  # reached the destination
    FAILED = "failed"  # entered a hazard
    TRUNCATED = "truncated"  # ran out of steps


class Outcome(StrEnum):
    MOVED = "moved"
    BLOCKED = "blocked"  # hit a wall or obstacle; position unchanged
    HAZARD = "hazard"
    GOAL = "goal"


REWARDS: dict[Outcome, int] = {
    Outcome.MOVED: -1,
    Outcome.BLOCKED: -2,
    Outcome.HAZARD: -20,
    Outcome.GOAL: 20,
}

_DELTAS: dict[Action, tuple[int, int]] = {
    Action.UP: (-1, 0),
    Action.DOWN: (1, 0),
    Action.LEFT: (0, -1),
    Action.RIGHT: (0, 1),
}

Position = tuple[int, int]


def position_dict(position: Position) -> dict[str, int]:
    return {"row": position[0], "col": position[1]}


@dataclass(frozen=True)
class Layout:
    start: Position
    goal: Position
    obstacles: frozenset[Position]
    hazards: frozenset[Position]
    size: int = GRID_SIZE

    @classmethod
    def from_map(cls, rows: list[str]) -> "Layout":
        """Build a layout from text rows using S, G, #, H and '.' characters."""
        if len(rows) != GRID_SIZE or any(len(row) != GRID_SIZE for row in rows):
            raise ValueError(f"Map must be {GRID_SIZE}x{GRID_SIZE}")
        cells: dict[str, list[Position]] = {char: [] for char in "SG#H."}
        for r, row in enumerate(rows):
            for c, char in enumerate(row):
                if char not in cells:
                    raise ValueError(f"Unknown map character: {char!r}")
                cells[char].append((r, c))
        if len(cells["S"]) != 1 or len(cells["G"]) != 1:
            raise ValueError("Map needs exactly one S and one G")
        return cls(
            start=cells["S"][0],
            goal=cells["G"][0],
            obstacles=frozenset(cells["#"]),
            hazards=frozenset(cells["H"]),
        )

    def contains(self, position: Position) -> bool:
        return 0 <= position[0] < self.size and 0 <= position[1] < self.size

    def cell_type(self, position: Position) -> str:
        if not self.contains(position):
            return "wall"
        if position in self.obstacles:
            return "obstacle"
        if position in self.hazards:
            return "hazard"
        if position == self.goal:
            return "goal"
        return "free"

    def to_map(self) -> list[str]:
        chars = {"obstacle": "#", "hazard": "H", "goal": "G", "free": "."}
        return [
            "".join(
                "S" if (r, c) == self.start else chars[self.cell_type((r, c))]
                for c in range(self.size)
            )
            for r in range(self.size)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "grid_size": self.size,
            "start": position_dict(self.start),
            "goal": position_dict(self.goal),
            "obstacles": [position_dict(p) for p in sorted(self.obstacles)],
            "hazards": [position_dict(p) for p in sorted(self.hazards)],
        }


DEFAULT_LAYOUT = Layout.from_map(
    [
        "S......",
        ".##.H..",
        "....#..",
        ".H#...H",
        "..#.#..",
        "#...H#.",
        "..H...G",
    ]
)


class RouteSimError(Exception):
    """An expected failure whose message is safe to show to the calling agent."""


class EpisodeFinishedError(RouteSimError):
    """Raised when a move is attempted after the episode has ended."""


@dataclass(frozen=True)
class StepResult:
    action: Action
    outcome: Outcome
    reward: int
    previous_position: Position
    position: Position


class GridWorld:
    """State of one episode."""

    def __init__(self, layout: Layout = DEFAULT_LAYOUT, max_steps: int = MAX_STEPS) -> None:
        self.layout = layout
        self.max_steps = max_steps
        self.reset()

    def reset(self) -> None:
        self.position: Position = self.layout.start
        self.step_count = 0
        self.cumulative_reward = 0
        self.status = Status.IN_PROGRESS
        self.history: list[StepResult] = []

    @property
    def done(self) -> bool:
        return self.status is not Status.IN_PROGRESS

    def path(self) -> list[Position]:
        """The cells the agent has moved through, in order, beginning at the start."""
        moves = [s.position for s in self.history if s.position != s.previous_position]
        return [self.layout.start, *moves]

    def target(self, action: Action) -> Position:
        d_row, d_col = _DELTAS[action]
        return (self.position[0] + d_row, self.position[1] + d_col)

    def neighbors(self) -> dict[str, str]:
        """The type of the cell next to the agent in each direction."""
        return {action.value: self.layout.cell_type(self.target(action)) for action in Action}

    def valid_actions(self) -> list[Action]:
        """Actions that change the position. Hazards are valid but end the episode."""
        if self.done:
            return []
        return [
            action
            for action in Action
            if self.layout.cell_type(self.target(action)) not in ("wall", "obstacle")
        ]

    def step(self, action: Action) -> StepResult:
        if self.done:
            raise EpisodeFinishedError(
                f"Episode has already ended with status '{self.status}'. "
                "Reset it or start a new episode."
            )
        previous = self.position
        target = self.target(action)
        cell = self.layout.cell_type(target)

        if cell in ("wall", "obstacle"):
            outcome = Outcome.BLOCKED
        else:
            self.position = target
            if cell == "hazard":
                outcome = Outcome.HAZARD
                self.status = Status.FAILED
            elif cell == "goal":
                outcome = Outcome.GOAL
                self.status = Status.SUCCEEDED
            else:
                outcome = Outcome.MOVED

        self.step_count += 1
        reward = REWARDS[outcome]
        self.cumulative_reward += reward
        if not self.done and self.step_count >= self.max_steps:
            self.status = Status.TRUNCATED

        result = StepResult(action, outcome, reward, previous, self.position)
        self.history.append(result)
        return result

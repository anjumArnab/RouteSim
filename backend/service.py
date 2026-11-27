"""Episode storage, lifecycle, activity logging and live event fan-out.

All state is held in memory. Every public tool method is synchronous and is
called from the server's single asyncio event loop, so each call is atomic.
Run the application as one process.
"""

import asyncio
import secrets
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.environment import (
    REWARDS,
    Action,
    GridWorld,
    Position,
    RouteSimError,
    Status,
    StepResult,
    position_dict,
)

MAX_EPISODES_PER_AGENT = 100
MAX_LOG_ENTRIES_PER_AGENT = 500
SUBSCRIBER_QUEUE_SIZE = 1000
MAX_LOGGED_STRING = 200


class EpisodeNotFoundError(RouteSimError):
    """The episode does not exist or belongs to a different agent."""


class InvalidActionError(RouteSimError):
    """The requested action is not one the environment supports."""


@dataclass
class Episode:
    episode_id: str
    owner: str
    created_at: str
    world: GridWorld = field(default_factory=GridWorld)


@dataclass
class _AgentSpace:
    """Everything belonging to one authenticated agent."""

    episodes: dict[str, Episode] = field(default_factory=dict)  # oldest first
    log: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_LOG_ENTRIES_PER_AGENT)
    )
    subscribers: set[asyncio.Queue] = field(default_factory=set)


# An operation receives the addressed episode (None for start_episode) and
# returns the episode it acted on together with the result for the agent.
Operation = Callable[[Episode | None], tuple[Episode, dict[str, Any]]]


class EpisodeService:
    def __init__(self) -> None:
        self._spaces: dict[str, _AgentSpace] = {}
        self._sequence = 0

    # ----- tool operations -------------------------------------------------

    def start_episode(self, agent: str) -> dict[str, Any]:
        def operation(_: Episode | None) -> tuple[Episode, dict[str, Any]]:
            episode = Episode(
                episode_id=f"ep_{secrets.token_hex(8)}",
                owner=agent,
                created_at=_now(),
            )
            self._space(agent).episodes[episode.episode_id] = episode
            return episode, _observation(episode)

        return self._call(agent, "start_episode", {}, operation)

    def reset_episode(self, agent: str, episode_id: str) -> dict[str, Any]:
        def operation(episode: Episode) -> tuple[Episode, dict[str, Any]]:
            episode.world.reset()
            return episode, _observation(episode)

        return self._call(agent, "reset_episode", {"episode_id": episode_id}, operation)

    def get_observation(self, agent: str, episode_id: str) -> dict[str, Any]:
        def operation(episode: Episode) -> tuple[Episode, dict[str, Any]]:
            return episode, _observation(episode)

        return self._call(agent, "get_observation", {"episode_id": episode_id}, operation)

    def inspect_environment(self, agent: str, episode_id: str) -> dict[str, Any]:
        def operation(episode: Episode) -> tuple[Episode, dict[str, Any]]:
            return episode, _inspection(episode)

        return self._call(agent, "inspect_environment", {"episode_id": episode_id}, operation)

    def move_agent(self, agent: str, episode_id: str, direction: str) -> dict[str, Any]:
        def operation(episode: Episode) -> tuple[Episode, dict[str, Any]]:
            try:
                action = Action(direction)
            except ValueError:
                options = ", ".join(a.value for a in Action)
                raise InvalidActionError(
                    f"Unknown direction '{_clip(direction)}'. Use one of: {options}."
                ) from None
            step = episode.world.step(action)
            return episode, {
                "action": step.action.value,
                "outcome": step.outcome.value,
                **_reward_fields(step),
                "previous_position": position_dict(step.previous_position),
                **_observation(episode),
            }

        arguments = {"episode_id": episode_id, "direction": direction}
        return self._call(agent, "move_agent", arguments, operation)

    def get_episode_status(self, agent: str, episode_id: str) -> dict[str, Any]:
        def operation(episode: Episode) -> tuple[Episode, dict[str, Any]]:
            return episode, _results(episode)

        return self._call(agent, "get_episode_status", {"episode_id": episode_id}, operation)

    # ----- dashboard access (read-only) ------------------------------------

    def snapshot(self, agent: str) -> dict[str, Any]:
        space = self._space(agent)
        return {
            "type": "snapshot",
            "agent": agent,
            "episodes": [_dashboard_state(e) for e in space.episodes.values()],
            "log": list(space.log),
        }

    @contextmanager
    def subscribe(self, agent: str) -> Iterator[asyncio.Queue]:
        """Receive this agent's future events. A None item means "reconnect"."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        subscribers = self._space(agent).subscribers
        subscribers.add(queue)
        try:
            yield queue
        finally:
            subscribers.discard(queue)

    # ----- internals -------------------------------------------------------

    def _space(self, agent: str) -> _AgentSpace:
        return self._spaces.setdefault(agent, _AgentSpace())

    def _call(
        self, agent: str, tool: str, arguments: dict[str, str], operation: Operation
    ) -> dict[str, Any]:
        """Run one tool call, then log it and publish it, whether it succeeds or not."""
        space = self._space(agent)
        episode_id = arguments.get("episode_id")
        episode = space.episodes.get(episode_id) if episode_id is not None else None
        before = _tracked_state(episode)
        try:
            if episode_id is not None and episode is None:
                raise EpisodeNotFoundError(
                    f"Episode '{_clip(episode_id)}' was not found for this agent. "
                    "Use start_episode to create one."
                )
            episode, result = operation(episode)
        except RouteSimError as error:
            self._record(space, tool, arguments, episode_id, episode, error=str(error))
            raise

        after = _tracked_state(episode)
        changes = [
            {"field": name, "before": before.get(name), "after": value}
            for name, value in after.items()
            if before.get(name) != value
        ]
        removed = []
        while len(space.episodes) > MAX_EPISODES_PER_AGENT:
            removed.append(space.episodes.pop(next(iter(space.episodes))).episode_id)
        self._record(
            space,
            tool,
            arguments,
            episode.episode_id,
            episode,
            result=result,
            changes=changes,
            removed=removed,
        )
        return result

    def _record(
        self,
        space: _AgentSpace,
        tool: str,
        arguments: dict[str, str],
        episode_id: str | None,
        episode: Episode | None,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        changes: list[dict[str, Any]] | None = None,
        removed: list[str] | None = None,
    ) -> None:
        self._sequence += 1
        entry = {
            "seq": self._sequence,
            "timestamp": _now(),
            "episode_id": _clip(episode_id) if episode_id is not None else None,
            "tool": tool,
            "arguments": {name: _clip(value) for name, value in arguments.items()},
            "ok": error is None,
            "result": result,
            "error": error,
            "reward": result.get("reward") if result else None,
            "reward_breakdown": result.get("reward_breakdown") if result else None,
            "coin_collected": result.get("coin_collected") if result else None,
            "changes": changes or [],
        }
        space.log.append(entry)
        event = {
            "type": "activity",
            "entry": entry,
            "episode": _dashboard_state(episode) if episode else None,
            "removed_episode_ids": removed or [],
        }
        for queue in list(space.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The reader is too far behind: drop it so it reconnects and
                # receives a fresh snapshot.
                space.subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: str) -> str:
    return value if len(value) <= MAX_LOGGED_STRING else value[:MAX_LOGGED_STRING] + "…"


def _tracked_state(episode: Episode | None) -> dict[str, Any]:
    if episode is None:
        return {}
    world = episode.world
    return {
        "position": position_dict(world.position),
        "step_count": world.step_count,
        "cumulative_reward": world.cumulative_reward,
        "coins_collected": len(world.collected_coins),
        "status": world.status.value,
    }


def _positions(positions: list[Position]) -> list[dict[str, int]]:
    return [position_dict(p) for p in positions]


def _coin_state(world: GridWorld) -> dict[str, Any]:
    return {
        "total_coins": len(world.layout.coins),
        "available_coins": _positions(world.available_coins()),
        "collected_coin_positions": _positions(world.collected_coins),
    }


def _reward_fields(step: StepResult) -> dict[str, Any]:
    coin = step.coin_collected
    return {
        "reward": step.reward,
        "reward_breakdown": dict(step.breakdown),
        "coin_collected": position_dict(coin) if coin else None,
    }


def _dashboard_state(episode: Episode) -> dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "created_at": episode.created_at,
        "layout": episode.world.layout.to_dict(),
        "max_steps": episode.world.max_steps,
        "path": _positions(episode.world.path()),
        **_tracked_state(episode),
        **_coin_state(episode.world),
    }


def _observation(episode: Episode) -> dict[str, Any]:
    world = episode.world
    return {
        "episode_id": episode.episode_id,
        **_tracked_state(episode),
        **_coin_state(world),
        "done": world.done,
        "steps_remaining": world.max_steps - world.step_count,
        "start": position_dict(world.layout.start),
        "goal": position_dict(world.layout.goal),
        "neighbors": world.neighbors(),
        "valid_actions": [a.value for a in world.valid_actions()],
    }


def _inspection(episode: Episode) -> dict[str, Any]:
    world = episode.world
    return {
        "episode_id": episode.episode_id,
        **world.layout.to_dict(),
        "coins_collected": len(world.collected_coins),
        **_coin_state(world),
        "map": world.layout.to_map(world.available_coins()),
        "legend": {
            "S": "start",
            "G": "goal",
            "C": "coin still available (move onto it to collect it, once per episode)",
            "#": "obstacle (impassable)",
            "H": "hazard (ends the episode)",
            ".": "free",
        },
        "coordinates": "row 0 is the top row, col 0 is the left column; 'up' decreases row",
        "actions": [a.value for a in Action],
        "max_steps": world.max_steps,
        "rewards": dict(REWARDS),
        "reward_rules": (
            "Every move earns 'step', plus 'collision', 'coin', 'goal' or 'hazard' if the "
            "move causes that. Coins are optional: reaching the goal ends the episode."
        ),
    }


def _results(episode: Episode) -> dict[str, Any]:
    world = episode.world
    return {
        "episode_id": episode.episode_id,
        **_tracked_state(episode),
        **_coin_state(world),
        "done": world.done,
        "reached_goal": world.status is Status.SUCCEEDED,
        "steps_remaining": world.max_steps - world.step_count,
        "trajectory": [
            {
                "step": number,
                "action": step.action.value,
                "outcome": step.outcome.value,
                **_reward_fields(step),
                "position": position_dict(step.position),
            }
            for number, step in enumerate(world.history, start=1)
        ],
    }

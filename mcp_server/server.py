"""MCP tools for RouteSim.

Each tool is a thin wrapper around EpisodeService. No simulation logic lives
here. The caller's identity comes from the bearer-token middleware.
"""

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from backend.environment import Action, RouteSimError
from backend.service import EpisodeService

INSTRUCTIONS = """\
RouteSim is a 7x7 grid navigation environment. Move the agent from the start to
the goal while avoiding obstacles (impassable) and hazards (end the episode).

1. Call start_episode and keep the returned episode_id.
2. Call inspect_environment to see the whole map and the reward rules.
3. Call move_agent one step at a time until the episode is done.
4. Call get_episode_status for the final result.

Always pass your own episode_id. Do not reuse an ID from another session.
"""

EpisodeId = Annotated[str, Field(description="The episode_id returned by start_episode.")]
# The enum is advertised to clients; the backend does the validation so that
# rejected directions also appear in the activity log.
Direction = Annotated[
    str,
    Field(
        description="Direction to move one cell.",
        json_schema_extra={"enum": [a.value for a in Action]},
    ),
]


def build_mcp_server(service: EpisodeService) -> MCPServer:
    server = MCPServer("routesim", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    changes_state = ToolAnnotations(read_only_hint=False, open_world_hint=False)

    def invoke(ctx: Context, operation: Callable[..., dict[str, Any]], *args: str) -> dict[str, Any]:
        agent: str = ctx.request_context.request.state.agent
        try:
            return operation(agent, *args)
        except RouteSimError as error:
            raise ToolError(str(error)) from error

    # Tools are async so they run on the event loop, which keeps calls atomic.

    @server.tool(annotations=changes_state)
    async def start_episode(ctx: Context) -> dict[str, Any]:
        """Start a new isolated episode and return its episode_id and first observation."""
        return invoke(ctx, service.start_episode)

    @server.tool(annotations=changes_state)
    async def reset_episode(episode_id: EpisodeId, ctx: Context) -> dict[str, Any]:
        """Reset an episode to the start position with zero steps and reward."""
        return invoke(ctx, service.reset_episode, episode_id)

    @server.tool(annotations=read_only)
    async def get_observation(episode_id: EpisodeId, ctx: Context) -> dict[str, Any]:
        """Get the current position, neighbouring cells, valid actions, steps and reward."""
        return invoke(ctx, service.get_observation, episode_id)

    @server.tool(annotations=read_only)
    async def inspect_environment(episode_id: EpisodeId, ctx: Context) -> dict[str, Any]:
        """Get the full map, start, goal, obstacles, hazards, actions and reward rules."""
        return invoke(ctx, service.inspect_environment, episode_id)

    @server.tool(annotations=changes_state)
    async def move_agent(
        episode_id: EpisodeId, direction: Direction, ctx: Context
    ) -> dict[str, Any]:
        """Move the agent one cell up, down, left or right and receive the reward."""
        return invoke(ctx, service.move_agent, episode_id, direction)

    @server.tool(annotations=read_only)
    async def get_episode_status(episode_id: EpisodeId, ctx: Context) -> dict[str, Any]:
        """Get the episode status, results and the trajectory of moves so far."""
        return invoke(ctx, service.get_episode_status, episode_id)

    return server

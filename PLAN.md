# RouteSim: structure and implementation plan

## Architecture

One Python process holds all state. The frontend is a read-only observer.

```
Claude Code / OpenCode ──MCP (Streamable HTTP, bearer token)──▶ /mcp ─┐
                                                                       ├─▶ EpisodeService ─▶ GridWorld
Browser dashboard ◀────── SSE (fetch, bearer token) ──────── /api/stream ┘   (in memory)
```

## Project structure

```
backend/
  environment.py     Grid layout, actions, transitions, rewards, episode status
  service.py         Episode storage, ownership, lifecycle, activity log, event fan-out
  auth.py            Bearer-token to agent-name authentication
  config.py          Environment-variable settings
  app.py             FastAPI app: /api/health, /api/stream, /mcp, static frontend
  __main__.py        `python -m backend`
mcp_server/
  server.py          MCP tool definitions that call EpisodeService
frontend/
  src/
    App.tsx          Layout, token entry, episode selection
    stream.ts        SSE connection with reconnect
    types.ts         Shapes of backend data
    components/      Grid, EpisodeStatus, ActivityLog
tests/
  test_environment.py   Environment rules
  test_backend.py       Service, auth, config, stream endpoint
  test_mcp_tools.py     Every MCP tool over real Streamable HTTP
  test_agent_flow.py    Full agent episode observed through the live stream
README.md
```

## Key decisions

| Topic | Decision |
|---|---|
| Process model | MCP server is mounted in the FastAPI app and calls the service in-process. Run exactly one worker. |
| Auth | `ROUTESIM_API_KEYS=name:token,...`. The server refuses to start without it. |
| Isolation | Random episode IDs, owned by the creating agent. Another agent gets "not found". No implicit "current episode". |
| Mutation | Only MCP tools change state. The HTTP API is read-only. |
| Live updates | SSE. A new connection gets a full snapshot, then one event per tool call. |
| Grid | Fixed 7x7 layout, `(row, col)`, row 0 at the top. |
| Rewards | step -1, blocked -2 (agent stays), hazard -20 (episode fails), goal +20. Truncated after 50 steps. |

## Tools

`start_episode`, `reset_episode`, `get_observation`, `inspect_environment`, `move_agent`, `get_episode_status`

## Steps

1. Environment and its tests.
2. Service, auth, FastAPI app, stream.
3. MCP tools and tests over real HTTP.
4. End-to-end agent flow test.
5. Frontend; type-check, build, and view in a real browser.
6. Verify registration from Claude Code and OpenCode; write README.

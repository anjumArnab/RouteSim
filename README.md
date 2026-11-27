# RouteSim

A small reinforcement learning environment that AI agents drive remotely over MCP, with a
live dashboard for watching them. An agent crosses a 7x7 grid from start to destination,
avoiding obstacles and hazards and deciding which coins are worth a detour. There are no
training algorithms here, only the environment.

```
Claude Code / OpenCode ──MCP (Streamable HTTP, bearer token)──▶ /mcp ─┐
                                                                       ├─▶ EpisodeService ─▶ GridWorld
Browser dashboard ◀────── Server-Sent Events (bearer token) ─ /api/stream ┘   (in memory)
```

The backend and MCP server run in one Python process, so MCP tools call the backend
directly. Only MCP tools can change the environment. The dashboard and HTTP API are read-only.

| Folder | Contents |
|---|---|
| `backend/` | Environment rules, episode service, authentication, FastAPI app |
| `mcp_server/` | MCP tool definitions (official Python SDK) |
| `frontend/` | React + Vite + TypeScript dashboard |
| `tests/` | Environment, backend, MCP tool and end-to-end flow tests |

## Requirements

- Python 3.11 or newer
- Node.js 22.12 or newer (to build the dashboard)

## Install

Run these from the project root.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt     # use requirements.txt on a server without tests
npm --prefix frontend install
npm --prefix frontend run build
Copy-Item .env.example .env
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt     # use requirements.txt on a server without tests
npm --prefix frontend install
npm --prefix frontend run build
cp .env.example .env
```

## Configure

Generate one token for each agent:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put them in `.env`:

```ini
ROUTESIM_API_KEYS=claude:<first token>,opencode:<second token>
ROUTESIM_HOST=127.0.0.1
ROUTESIM_PORT=8000
```

| Variable | Default | Meaning |
|---|---|---|
| `ROUTESIM_API_KEYS` | required | `name:token` pairs, comma separated. Tokens need 16+ characters. |
| `ROUTESIM_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` accepts remote connections. |
| `ROUTESIM_PORT` | `8000` | Port |

Real environment variables override `.env`.

## Start

```bash
python -m backend
```

- Dashboard: <http://127.0.0.1:8000>
- MCP endpoint: `http://127.0.0.1:8000/mcp` (no trailing slash)

Open the dashboard and paste the token of the agent you want to watch. It shows:

- the grid with the agent's path, the start (**1**), the destination (**2**), available coins
  (gold **$**) and collected coins (dashed ring with **✓**);
- episode ID, status, coins collected out of the total, steps taken and cumulative reward;
- a live log of that agent's tool calls, including coin pickups and the parts of each reward.

It follows whichever episode was used last, or you can pick one from the list. The token is
kept only in that browser tab.

### Frontend development

For hot reload, run the backend as above, then in a second terminal:

```bash
npm --prefix frontend run dev
```

Open <http://localhost:5173>. Vite forwards `/api` to port 8000.

## Connect an agent

Replace `http://127.0.0.1:8000` with your public `https://` address for a remote server.

### Claude Code

PowerShell:

```powershell
$env:ROUTESIM_TOKEN = "<the claude token>"
claude mcp add --transport http routesim http://127.0.0.1:8000/mcp --header "Authorization: Bearer $env:ROUTESIM_TOKEN"
claude mcp list
```

Linux / macOS:

```bash
export ROUTESIM_TOKEN="<the claude token>"
claude mcp add --transport http routesim http://127.0.0.1:8000/mcp --header "Authorization: Bearer $ROUTESIM_TOKEN"
claude mcp list
```

`claude mcp list` should report `routesim ... ✔ Connected`. The command saves the token in
your private Claude Code config for the current project. Add `--scope user` to use the
server from every project, and remove it with `claude mcp remove routesim`.

### OpenCode (2.x)

Create `opencode.json` in the project where you run OpenCode:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "routesim": {
        "type": "remote",
        "url": "http://127.0.0.1:8000/mcp",
        "oauth": false,
        "headers": { "Authorization": "Bearer {env:ROUTESIM_TOKEN}" }
      }
    }
  }
}
```

OpenCode runs a background service, and that service only sees environment variables that
existed **when it started**. Set the token first, then restart the service:

```powershell
$env:ROUTESIM_TOKEN = "<the opencode token>"      # bash: export ROUTESIM_TOKEN="..."
opencode service restart
opencode mcp list
```

`opencode mcp list` should report `✓ routesim connected`. Right after a restart it may
print `No MCP servers configured` while the project loads; run it again after a few seconds.

This format is for OpenCode 2.x (checked with 2.3.0). Older 1.x releases use a different
layout, so follow their documentation.

### Try it

Ask the agent in plain language, for example:

> Using the routesim tools, start a new episode and inspect the environment. Reach the
> destination with the highest total reward you can: decide which coins are worth collecting
> and stay off the hazards. Then report the episode results.

Watch the dashboard (with the same agent's token) while it works. Run the agent from a folder
that does not contain this source code, because the tests include the best route.

## The environment

```
     0 1 2 3 4 5 6
  0  S . . . . . C      S  start (0, 0)
  1  . # # C H . .      G  destination (6, 6)
  2  . C . . # C .      C  coin
  3  . H # . . . H      #  obstacle, impassable
  4  . . # . # . .      H  hazard, ends the episode
  5  # . . . H # .      .  free
  6  C . H . . . G
```

Positions are `(row, col)` with row 0 at the top. The layout is the same in every episode.

### Rules

- The agent moves one cell at a time: up, down, left or right.
- Moving onto a coin collects it. It disappears and cannot reward the agent again in that
  episode. `reset_episode` and new episodes start with all five coins.
- Coins are optional. Reaching the destination ends the episode, however many are left.
- Rewards already earned are kept if the episode later fails or runs out of steps.

| Status | Cause |
|---|---|
| `in_progress` | The episode is running |
| `succeeded` | The agent reached the destination |
| `failed` | The agent entered a hazard |
| `truncated` | Still not finished after 50 moves |

No moves are accepted once an episode has ended.

### Rewards

Every move costs 1. Whatever the move causes is added to that cost, and the cumulative
reward is the sum over all moves.

| Part | Value | Applies when |
|---|---|---|
| `step` | -1 | Every move |
| `collision` | -1 | A wall or obstacle blocks the move (position unchanged) |
| `coin` | +5 | The move collects a coin |
| `goal` | +20 | The move reaches the destination |
| `hazard` | -20 | The move enters a hazard |

| Move result (`outcome`) | Total reward |
|---|---|
| `moved` | -1 |
| `blocked` | -2 |
| `coin` | +4 |
| `goal` | +19 |
| `hazard` | -21 |

Running out of steps has no extra penalty; the 50 step costs already count against it.

### Strategies

The shortest route does not pay the most, and neither does collecting everything.

| Strategy | Moves | Coins | Total reward |
|---|---|---|---|
| Shortest route to the destination | 12 | 0 of 5 | 8 |
| Collect every coin | 30 | 5 of 5 | 15 |
| Balanced: skip the far coin at (6, 0) | 18 | 4 of 5 | **22** (best possible) |

The coin at (6, 0) costs 12 extra moves and returns only 5.
[tests/test_coin_strategies.py](tests/test_coin_strategies.py) plays all three routes and
searches every reachable state to confirm that 22 is the maximum. The layout and reward
values are in [backend/environment.py](backend/environment.py); changing either changes
which strategy wins.

## MCP tools

| Tool | Arguments | Returns |
|---|---|---|
| `start_episode` | none | New `episode_id` and first observation |
| `reset_episode` | `episode_id` | Observation after returning to the start |
| `get_observation` | `episode_id` | The observation described below |
| `inspect_environment` | `episode_id` | Current map, obstacles, hazards, coins, reward rules, step limit |
| `move_agent` | `episode_id`, `direction` (`up`, `down`, `left`, `right`) | Outcome, reward, reward parts, coin collected, new observation |
| `get_episode_status` | `episode_id` | Status, totals, coins and the trajectory |

### Observation

| Field | Meaning |
|---|---|
| `episode_id`, `status`, `done` | Which episode, and whether it has ended |
| `position`, `start`, `goal` | Cells as `{"row": r, "col": c}` |
| `neighbors` | Per direction: `free`, `coin`, `goal`, `hazard`, `obstacle` or `wall` |
| `valid_actions` | Directions that change the position |
| `available_coins` | Positions of the coins still on the grid |
| `collected_coin_positions` | Coins picked up, in order |
| `coins_collected`, `total_coins` | Counts |
| `step_count`, `steps_remaining`, `cumulative_reward` | Progress so far |

`valid_actions` includes moves into hazards, so check `neighbors` before moving.

## Authentication and isolation

- Every `/mcp` and `/api/stream` request needs `Authorization: Bearer <token>`.
  Only `/api/health` and the dashboard's static files are public.
- Each episode has a random ID and belongs to the agent whose token created it. Other
  agents get "not found" for it, and never see it on their dashboard.
- Give every agent its own token. Sessions sharing a token are the same agent: each
  `start_episode` still returns a separate episode, but one session could use another's ID.

## Deploy

1. On the server, install and build as above and set `.env`.
2. Run `python -m backend` under a process manager (systemd, NSSM, or similar).
3. Put HTTPS in front of it. Tokens travel in a header, so never expose plain HTTP to an
   untrusted network.

**Caddy** (obtains certificates automatically). Keep `ROUTESIM_HOST=127.0.0.1`:

```
routesim.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx**, inside your TLS `server` block. Buffering must be off for live updates:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

**No domain:** keep the server on `127.0.0.1` and tunnel from the client machine with
`ssh -L 8000:127.0.0.1:8000 user@server`, then use the local URLs above. On a trusted
private network only, you can instead set `ROUTESIM_HOST=0.0.0.0` and allow the port
through the firewall.

Limits of in-memory storage:

- Run **exactly one process**. Do not add workers or replicas; they would not share episodes.
- A restart deletes all episodes and logs.
- Each agent keeps its newest 100 episodes and last 500 log entries.

## Tests

```bash
pytest
npm --prefix frontend run build      # type-checks and builds the dashboard
```

The MCP tests start the real application on a free local port and connect with the official
MCP client over Streamable HTTP.

| File | Covers |
|---|---|
| `tests/test_environment.py` | Layout, moves, rewards, coins, terminal states, reset |
| `tests/test_backend.py` | Isolation, activity log, auth, configuration, live stream |
| `tests/test_mcp_tools.py` | All six tools, errors, cross-agent access |
| `tests/test_agent_flow.py` | An agent fails, resets and succeeds while the stream is checked |
| `tests/test_coin_strategies.py` | Shortest, collect-all and balanced routes, directly and over MCP |

## Troubleshooting

| Symptom | Check |
|---|---|
| PowerShell says `running scripts is disabled on this system` | Run the `.cmd` version (`opencode.cmd`, `npm.cmd`, `claude.cmd`). Instead of activating the virtual environment, call `.\.venv\Scripts\python -m ...` directly |
| `Configuration error: ROUTESIM_API_KEYS is not set` | Create `.env` and add at least one `name:token` |
| HTTP 401 from a client | The token matches one in `ROUTESIM_API_KEYS`; for OpenCode, restart its service after setting the variable |
| `http://127.0.0.1:8000` shows "Not Found" | Build the dashboard, then restart the server |
| Dashboard is empty while an agent runs | The dashboard token must be that agent's token |
| Dashboard shows no coins after an update | Restart the server, then reload the page |
| Live updates arrive late or in bursts | Turn off response buffering in your proxy |
| "Episode ... was not found" | The server restarted, or the ID belongs to another agent. Start a new episode |

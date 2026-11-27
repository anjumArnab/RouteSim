import { useState, type FormEvent } from "react";
import { ActivityLog } from "./components/ActivityLog";
import { EpisodeStatus } from "./components/EpisodeStatus";
import { Grid } from "./components/Grid";
import { useDashboard, type Connection } from "./stream";
import type { Episode, LogEntry } from "./types";

// Kept for this browser tab only.
const TOKEN_KEY = "routesim-token";

const CONNECTION_LABELS: Record<Connection, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  unauthorized: "Invalid token",
};

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) ?? "");

  function connect(value: string) {
    sessionStorage.setItem(TOKEN_KEY, value);
    setToken(value);
  }

  function disconnect() {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
  }

  return token ? (
    <Dashboard token={token} onDisconnect={disconnect} />
  ) : (
    <TokenForm onSubmit={connect} />
  );
}

function TokenForm({ onSubmit }: { onSubmit: (token: string) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = String(new FormData(event.currentTarget).get("token")).trim();
    if (token) onSubmit(token);
  }

  return (
    <main className="token-screen">
      <h1>RouteSim</h1>
      <form className="panel" onSubmit={submit}>
        <label htmlFor="token">API token of the agent to monitor</label>
        <input id="token" name="token" type="password" autoComplete="off" required autoFocus />
        <button type="submit">Connect</button>
      </form>
    </main>
  );
}

/** The episode touched by the most recent tool call. */
function latestEpisode(episodes: Episode[], log: LogEntry[]): Episode | undefined {
  for (let i = log.length - 1; i >= 0; i--) {
    const match = episodes.find((e) => e.episode_id === log[i].episode_id);
    if (match) return match;
  }
  return episodes.at(-1);
}

function Dashboard({ token, onDisconnect }: { token: string; onDisconnect: () => void }) {
  const { data, connection } = useDashboard(token);
  const [pinnedId, setPinnedId] = useState(""); // "" follows the latest activity
  const { episodes, log } = data;

  const selected =
    episodes.find((e) => e.episode_id === pinnedId) ?? latestEpisode(episodes, log);
  const known = new Set(episodes.map((e) => e.episode_id));
  // Rejected calls that name no existing episode stay visible too.
  const entries = log.filter(
    (e) => e.episode_id === selected?.episode_id || (!e.ok && !known.has(e.episode_id ?? "")),
  );

  return (
    <main>
      <header>
        <h1>RouteSim</h1>
        <span className={`connection ${connection}`} role="status">
          {CONNECTION_LABELS[connection]}
        </span>
        {data.agent && <span className="muted">agent: {data.agent}</span>}
        <span className="spacer" />
        {episodes.length > 0 && (
          <label>
            Episode{" "}
            <select
              value={known.has(pinnedId) ? pinnedId : ""}
              onChange={(event) => setPinnedId(event.target.value)}
            >
              <option value="">Follow latest activity</option>
              {[...episodes].reverse().map((e) => (
                <option key={e.episode_id} value={e.episode_id}>
                  {e.episode_id} ({e.status.replace("_", " ")})
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" onClick={onDisconnect}>
          Disconnect
        </button>
      </header>

      {connection === "unauthorized" ? (
        <p className="panel">The server rejected this token. Disconnect and try another.</p>
      ) : (
        <div className="columns">
          <section className="panel">
            {selected ? (
              <>
                <EpisodeStatus episode={selected} />
                <Grid episode={selected} />
              </>
            ) : (
              <p className="muted">
                {connection === "live"
                  ? "Waiting for the agent to start an episode."
                  : "Waiting for the server."}
              </p>
            )}
          </section>
          <ActivityLog entries={entries} />
        </div>
      )}
    </main>
  );
}

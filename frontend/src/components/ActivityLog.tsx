import type { LogEntry, StateChange } from "../types";

const FIELD_NAMES: Record<StateChange["field"], string> = {
  position: "position",
  step_count: "steps",
  cumulative_reward: "total reward",
  coins_collected: "coins",
  status: "status",
};

function formatValue(value: StateChange["after"]): string {
  if (typeof value === "object") return `(${value.row}, ${value.col})`;
  return String(value);
}

const signed = (n: number) => (n > 0 ? `+${n}` : String(n));

/** The parts of a reward as reported by the backend, when there is more than one. */
function breakdownText(breakdown: LogEntry["reward_breakdown"]): string {
  const parts = Object.entries(breakdown ?? {});
  if (parts.length < 2) return "";
  return ` (${parts.map(([name, value]) => `${name} ${signed(value)}`).join(", ")})`;
}

function Entry({ entry }: { entry: LogEntry }) {
  const args = Object.entries(entry.arguments);
  const outcome = entry.result?.outcome;
  return (
    <li className={entry.ok ? "entry" : "entry error"}>
      <div className="entry-head">
        <time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleTimeString()}</time>
        <strong className="mono">{entry.tool}</strong>
        <span className={entry.ok ? "result ok" : "result failed"}>
          {entry.ok ? "✓ ok" : "✕ error"}
          {typeof outcome === "string" && `: ${outcome}`}
        </span>
        {entry.reward !== null && (
          <span className="reward">
            reward {signed(entry.reward)}
            {breakdownText(entry.reward_breakdown)}
          </span>
        )}
      </div>
      <div className="mono muted">
        {args.length === 0
          ? "no arguments"
          : args.map(([name, value]) => `${name}=${JSON.stringify(value)}`).join("  ")}
      </div>
      {entry.error && <div className="error-text">{entry.error}</div>}
      {entry.coin_collected && (
        <div className="coin-event">Coin collected at {formatValue(entry.coin_collected)}</div>
      )}
      {entry.changes.length > 0 && (
        <ul className="changes">
          {entry.changes.map((change) => (
            <li key={change.field}>
              {FIELD_NAMES[change.field]}:{" "}
              {/* A new episode has no earlier value to show. */}
              {change.before !== null && `${formatValue(change.before)} to `}
              <b>{formatValue(change.after)}</b>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** Newest call first, so live updates appear without scrolling. */
export function ActivityLog({ entries }: { entries: LogEntry[] }) {
  return (
    <section className="panel log">
      <h2>Activity log</h2>
      {entries.length === 0 ? (
        <p className="muted">No tool calls yet.</p>
      ) : (
        <ol aria-live="polite">
          {[...entries].reverse().map((entry) => (
            <Entry key={entry.seq} entry={entry} />
          ))}
        </ol>
      )}
    </section>
  );
}

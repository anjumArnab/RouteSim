import type { Episode, Status } from "../types";

const STATUS: Record<Status, { icon: string; label: string }> = {
  in_progress: { icon: "●", label: "In progress" },
  succeeded: { icon: "✓", label: "Succeeded" },
  failed: { icon: "✕", label: "Failed" },
  truncated: { icon: "!", label: "Truncated" },
};

export function EpisodeStatus({ episode }: { episode: Episode }) {
  const status = STATUS[episode.status];
  return (
    <dl className="stats">
      <div className="wide">
        <dt>Episode ID</dt>
        <dd className="mono">{episode.episode_id}</dd>
      </div>
      <div>
        <dt>Status</dt>
        <dd>
          <span className={`status-icon ${episode.status}`} aria-hidden="true">
            {status.icon}
          </span>{" "}
          {status.label}
        </dd>
      </div>
      <div>
        <dt>Steps</dt>
        <dd>
          {episode.step_count} <small>/ {episode.max_steps}</small>
        </dd>
      </div>
      <div>
        <dt>Cumulative reward</dt>
        <dd>{episode.cumulative_reward}</dd>
      </div>
    </dl>
  );
}

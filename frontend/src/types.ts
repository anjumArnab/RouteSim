// Shapes of the data sent by GET /api/stream. The backend is the source of truth.

export interface Position {
  row: number;
  col: number;
}

export interface Layout {
  grid_size: number;
  start: Position;
  goal: Position;
  obstacles: Position[];
  hazards: Position[];
}

export type Status = "in_progress" | "succeeded" | "failed" | "truncated";

export interface Episode {
  episode_id: string;
  created_at: string;
  layout: Layout;
  max_steps: number;
  position: Position;
  // Cells moved through, beginning at the start. Absent if the server predates it.
  path?: Position[];
  // Coin fields are likewise absent from a server that predates coins.
  total_coins?: number;
  coins_collected?: number;
  available_coins?: Position[];
  collected_coin_positions?: Position[];
  step_count: number;
  cumulative_reward: number;
  status: Status;
}

export interface StateChange {
  field: "position" | "step_count" | "cumulative_reward" | "coins_collected" | "status";
  before: Position | number | string | null;
  after: Position | number | string;
}

export interface LogEntry {
  seq: number;
  timestamp: string;
  episode_id: string | null;
  tool: string;
  arguments: Record<string, string>;
  ok: boolean;
  result: Record<string, unknown> | null;
  error: string | null;
  reward: number | null;
  reward_breakdown?: Record<string, number> | null; // the named parts of the reward
  coin_collected?: Position | null;
  changes: StateChange[];
}

export type StreamEvent =
  | { type: "snapshot"; agent: string; episodes: Episode[]; log: LogEntry[] }
  | {
      type: "activity";
      entry: LogEntry;
      episode: Episode | null;
      removed_episode_ids: string[];
    };

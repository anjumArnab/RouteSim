import { useEffect, useReducer, useState } from "react";
import type { Episode, LogEntry, StreamEvent } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "unauthorized";

export interface DashboardData {
  agent: string | null;
  episodes: Episode[]; // oldest first
  log: LogEntry[]; // oldest first
}

const EMPTY: DashboardData = { agent: null, episodes: [], log: [] };
const MAX_LOG_ENTRIES = 500;
const RETRY_MS = 2000;
// The server sends a keep-alive every 15 s, so longer silence means a dead link.
const SILENCE_LIMIT_MS = 45000;

function reduce(data: DashboardData, event: StreamEvent | null): DashboardData {
  if (event === null) return EMPTY;
  if (event.type === "snapshot") {
    return { agent: event.agent, episodes: event.episodes, log: event.log };
  }
  const { entry, episode, removed_episode_ids: removed } = event;
  let episodes = data.episodes.filter((e) => !removed.includes(e.episode_id));
  if (episode) {
    const known = episodes.some((e) => e.episode_id === episode.episode_id);
    episodes = known
      ? episodes.map((e) => (e.episode_id === episode.episode_id ? episode : e))
      : [...episodes, episode];
  }
  return { ...data, episodes, log: [...data.log, entry].slice(-MAX_LOG_ENTRIES) };
}

// EventSource cannot send an Authorization header, so read the stream with fetch.
async function readStream(
  token: string,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
): Promise<"unauthorized" | "closed"> {
  const silence = new AbortController();
  let timer = setTimeout(() => silence.abort(), SILENCE_LIMIT_MS);
  try {
    const response = await fetch("/api/stream", {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.any([signal, silence.signal]),
    });
    if (response.status === 401) return "unauthorized";
    if (!response.ok || !response.body) return "closed";

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return "closed";
      clearTimeout(timer);
      timer = setTimeout(() => silence.abort(), SILENCE_LIMIT_MS);
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        if (frame.startsWith("data: ")) onEvent(JSON.parse(frame.slice("data: ".length)));
      }
    }
  } finally {
    clearTimeout(timer);
  }
}

/** Keep a live copy of the backend's data for the agent that owns `token`. */
export function useDashboard(token: string) {
  const [data, dispatch] = useReducer(reduce, EMPTY);
  const [connection, setConnection] = useState<Connection>("connecting");

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    dispatch(null);
    setConnection("connecting");

    async function run() {
      // Header values must be printable ASCII; anything else cannot be a token.
      if (!/^[\x21-\x7e]+$/.test(token)) return setConnection("unauthorized");
      while (!signal.aborted) {
        try {
          const outcome = await readStream(token, signal, (event) => {
            if (signal.aborted) return;
            setConnection("live");
            dispatch(event);
          });
          if (outcome === "unauthorized") return setConnection("unauthorized");
        } catch {
          // Network failure: fall through and retry.
        }
        if (signal.aborted) return;
        setConnection("reconnecting");
        await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
      }
    }

    void run();
    return () => controller.abort();
  }, [token]);

  return { data, connection };
}

/* Talking to the live backend.
 *
 * `runLive` opens the server-sent-event stream and hands each event to a
 * callback. The server sends the whole run record after every step, so there is
 * no merging to do here: the caller just keeps the newest record and re-renders.
 *
 * Written against `fetch` + a ReadableStream rather than `EventSource`, because
 * EventSource cannot send headers, cannot be aborted cleanly, and reconnects on
 * its own - which for a run that costs real compute would silently start a
 * second one. */

import { Run } from "./schema.js";

const DEFAULT_API = "";  // none given: main.js tries the page's own origin first

export function apiFromLocation(search = window.location.search) {
  return new URLSearchParams(search).get("api") ?? DEFAULT_API;
}

/** GET /datasets. Returns [] when there is no backend to ask. */
export async function fetchDatasets(api) {
  if (!api) return [];
  const response = await fetch(`${trim(api)}/datasets`, {
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error(`datasets: ${response.status}`);
  return (await response.json()).datasets ?? [];
}

export async function health(api) {
  if (!api) return null;
  const response = await fetch(`${trim(api)}/health`, { signal: timeout(4000) });
  return response.ok ? await response.json() : null;
}

/**
 * Stream one run.
 *
 * `onEvent({type, ...})` fires for start / step_start / step_done / error /
 * done. A `record` on the event is wrapped as a Run before it is passed on.
 * Returns an abort function - calling it stops reading, which is the only
 * honest thing the page can do, since the run itself is already underway
 * server-side.
 */
export function runLive({ api, dataset, context = "human PBMC", token, onEvent }) {
  const controller = new AbortController();
  const params = new URLSearchParams({ dataset, context });
  if (token) params.set("token", token);

  (async () => {
    let response;
    try {
      response = await fetch(`${trim(api)}/run?${params}`, {
        headers: { accept: "text/event-stream" },
        signal: controller.signal,
      });
    } catch (err) {
      onEvent({ type: "error", message: `could not reach the backend: ${err.message}` });
      return;
    }
    if (!response.ok || !response.body) {
      onEvent({ type: "error", message: await describe(response) });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line; a partial frame stays in
        // the buffer until the rest of it arrives.
        let split;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const event = parseFrame(frame);
          if (event) onEvent(hydrate(event));
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        onEvent({ type: "error", message: `stream ended: ${err.message}` });
      }
    }
  })();

  return () => controller.abort();
}

function parseFrame(frame) {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function hydrate(event) {
  return event.record ? { ...event, run: new Run(event.record) } : event;
}

async function describe(response) {
  try {
    const body = await response.json();
    return body.detail ?? `backend returned ${response.status}`;
  } catch {
    return `backend returned ${response.status}`;
  }
}

function trim(api) {
  return String(api).replace(/\/+$/, "");
}

function timeout(ms) {
  const c = new AbortController();
  setTimeout(() => c.abort(), ms);
  return c.signal;
}

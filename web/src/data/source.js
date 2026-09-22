/* Where a *saved* run record comes from.
 *
 * This is the replay path only. `?api=` is something else entirely - the live
 * backend, handled in `live.js` - so it is deliberately not read here.
 *
 *   ?run=<url>   an exported record served from anywhere (CORS permitting)
 *   bundled      data/run-demo.json, shipped with the site
 *
 * The bundled record is a real run of this pipeline, not a mock-up. Whether it
 * is a keyed run or an offline one is stated in its own `provenance` field, so
 * the page never has to guess what it is showing. */

import { Run } from "./schema.js";

export const DEFAULT_RECORD = "data/run-demo.json";

export function sourceFromLocation(search = window.location.search) {
  const url = new URLSearchParams(search).get("run");
  return url ? { kind: "url", url } : { kind: "bundled", url: DEFAULT_RECORD };
}

export async function loadRun(source = sourceFromLocation()) {
  const response = await fetch(source.url, { headers: { accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`could not load run record from ${source.url} (${response.status})`);
  }
  const raw = await response.json();
  const run = new Run(raw);
  run.source = source;
  return run;
}

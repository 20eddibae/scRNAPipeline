/* The top bar: what this is, which run is on screen, and who decided what. */

import { APP } from "../config.js";
import { h, chip } from "./dom.js";

export function renderHeader(root, run, { onReplay } = {}) {
  const t = run.tally();
  const bar = h("div", { class: "topbar-inner" },
    h("div", { class: "brand" },
      h("h1", { text: APP.title }),
      h("span", { class: "sub", text: APP.tagline }),
    ),
    chip(run.dataset, "mono"),
    chip(`${run.mode} order`, "mono"),
    chip(`${t.jev}/${t.total} by Jev`, "jev"),
    chip(`${t.fallback} default`, "fallback"),
    chip(`floor ${run.confidenceFloor.toFixed(2)}`, "mono"),
    onReplay ? h("button", { class: "chip modal", onClick: onReplay },
      h("span", { class: "dot" }), "Replay run") : null,
  );
  root.replaceChildren(bar);
}

/** The honesty banner. Shown whenever the record is not a live keyed run. */
export function renderBanner(root, run) {
  if (!run.demo && !run.provenance) { root.replaceChildren(); return; }
  const text = run.provenance ||
    "Demo record: a real offline run of this pipeline, every decision taken at " +
    "its declared default. Not a scored claim about Jev.";
  root.replaceChildren(h("div", { class: "banner-inner" }, text));
}

/* The top bar: the name, and which run is on screen. */

import { APP } from "../config.js";
import { h } from "./dom.js";

export function renderHeader(root, run, { onReplay } = {}) {
  const t = run?.tally();
  root.replaceChildren(h("div", { class: "topbar-inner" },
    h("div", { class: "brand" },
      h("h1", { text: APP.title }),
      h("span", { class: "sub", text: APP.tagline }),
    ),
    run ? h("div", { class: "run-meta" },
      h("b", { text: run.dataset }),
      ` · ${t.total} decisions, ${t.jev} by Jev, ${t.fallback} default`,
      onReplay ? " · " : null,
      onReplay ? h("button", { class: "linklike", onClick: onReplay }, "step through") : null,
    ) : null,
  ));
}

/** A one-line note about where the record came from, when it says anything. */
export function renderBanner(root, run) {
  const text = run?.provenance || (run?.demo
    ? "Demo record: an offline run where every decision took its declared default."
    : "");
  if (text) root.replaceChildren(h("div", { class: "banner-inner", text }));
  else root.replaceChildren();
}

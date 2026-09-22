/* Claude's notes for a step, other than question framing (which is shown under
 * the decision it framed). */

import { h } from "./dom.js";

export function renderReasoning(entries) {
  if (!entries.length) return null;
  return h("div", {},
    h("h3", { class: "section", text: "Claude's notes" }),
    entries.map((e) => h("p", { class: "lede", text: e.text })),
  );
}

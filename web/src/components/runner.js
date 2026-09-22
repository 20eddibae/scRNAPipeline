/* The run bar: pick a dataset, press Run, see the status.
 *
 * It owns nothing but its own DOM; the caller decides what each action does. */

import { h } from "./dom.js";

export function renderRunner(root, {
  api, datasets, status, running, dataset, onDataset, onRun, onStop, onApi, onFollow,
}) {
  const chosen = datasets.find((d) => d.name === dataset);

  root.replaceChildren(h("div", { class: "runner" },
    h("label", { for: "dataset", text: "Dataset" }),
    h("select", {
      id: "dataset", class: "control",
      disabled: running || !datasets.length,
      onChange: (e) => onDataset(e.target.value),
    },
      datasets.length
        ? datasets.map((d) => h("option", { value: d.name, selected: d.name === dataset }, d.name))
        : h("option", { value: "" }, "no backend"),
    ),
    running
      ? h("button", { class: "control", onClick: onStop }, "Stop watching")
      : h("button", { class: "control primary", disabled: !api || !dataset, onClick: onRun }, "Run"),
    statusLine(status, api),
    onFollow ? h("button", { class: "linklike", onClick: onFollow,
      title: "you clicked away mid-run; go back to watching it live" }, "follow the run") : null,
    h("button", { class: "linklike", onClick: onApi, title: "change the backend URL" },
      api ? (api === "." ? "this server" : shorten(api)) : "connect a backend"),
    chosen?.blurb ? h("div", { class: "blurb", text: chosen.blurb }) : null,
    !api ? h("div", { class: "blurb", text:
      "No backend connected. Run `uvicorn scrnapipeline.server:app --port 8000` to start one." }) : null,
  ));
}

function statusLine(status, api) {
  let cls = "", text = "saved run";
  if (api) {
    if (!status) { text = "connecting…"; }
    else if (status.error) { cls = "err"; text = status.error; }
    else if (status.running) { cls = "busy"; text = status.text ?? "running"; }
    else { cls = "ok"; text = status.text ?? "ready"; }
  }
  return h("span", { class: `status ${cls}`.trim() }, h("span", { class: "dot" }), text);
}

function shorten(api) {
  try {
    return new URL(api, window.location.origin).host || api;
  } catch {
    return api;
  }
}

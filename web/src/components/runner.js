/* The control bar: pick a dataset, press Run, watch it go.
 *
 * It owns nothing but its own DOM. Which backend to call, what to do with each
 * event and what is on screen are all the caller's; this renders the controls
 * and reports intent. */

import { chip, h } from "./dom.js";

export function renderRunner(root, {
  api, datasets, status, running, dataset, onDataset, onRun, onStop, onApi,
}) {
  const picker = h("select", {
    class: "control",
    disabled: running || !datasets.length,
    onChange: (e) => onDataset(e.target.value),
    "aria-label": "dataset",
  },
    datasets.length
      ? datasets.map((d) => h("option", { value: d.name, selected: d.name === dataset },
                              d.name))
      : h("option", { value: "" }, "no backend connected"),
  );

  const chosen = datasets.find((d) => d.name === dataset);

  const bar = h("div", { class: "runner" },
    h("div", { class: "runner-row" },
      h("span", { class: "runner-label", text: "dataset" }),
      picker,
      running
        ? h("button", { class: "control danger", onClick: onStop }, "Stop watching")
        : h("button", { class: "control primary", disabled: !api || !dataset,
                        onClick: onRun }, "Run pipeline"),
      h("span", { class: "spacer", style: "margin-left:auto" }),
      statusChip(status, api),
      h("button", { class: "chip", onClick: onApi, title: "change the backend URL" },
        api ? shorten(api) : "connect a backend"),
    ),
    chosen?.blurb ? h("div", { class: "runner-blurb", text: chosen.blurb }) : null,
    !api ? h("div", { class: "runner-blurb", text:
      "Nothing is running yet — this is a replay of a saved run. Point the page " +
      "at a backend to run the pipeline for real: locally with " +
      "`uvicorn scrnapipeline.server:app --port 8000`, or at the deployed Modal " +
      "URL." }) : null,
  );

  root.replaceChildren(bar);
}

function statusChip(status, api) {
  if (!api) return chip("replay", "fallback");
  if (!status) return chip("checking…", "fallback");
  if (status.error) return chip(status.error, "fallback");
  if (status.running) return chip(status.text ?? "running", "modal");
  return chip(status.text ?? "ready", "jev");
}

function shorten(api) {
  try {
    return new URL(api, window.location.origin).host || api;
  } catch {
    return api;
  }
}

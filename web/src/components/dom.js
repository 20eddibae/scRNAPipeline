/* Four helpers the components share. Keeps every component free of innerHTML
 * for anything that comes out of a run record. */

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = String(v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function chip(text, variant) {
  return h("span", { class: `chip ${variant ?? ""}`.trim() },
    variant && variant !== "mono" ? h("span", { class: "dot" }) : null, text);
}

export function sectionLabel(text) {
  return h("div", { class: "section-label", text });
}

export function replace(parent, ...nodes) {
  parent.replaceChildren(...nodes.flat().filter(Boolean));
  return parent;
}

export function fmt(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : String(value);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

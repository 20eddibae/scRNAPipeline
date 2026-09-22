/* Plays a decision out in order: Claude's framing types itself out, is handed
 * to Jev, and Jev's answer fills in. The text is the recorded framing, replayed
 * at reading speed; nothing here invents content.
 *
 * Each decision plays once per run: once it has started, a re-render (the live
 * stream sends a fresh record after every step) shows it finished rather than
 * typing it again from the first character. */

const played = new Set();
let skipAll = false;

export const motionOff = () =>
  skipAll || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function hasPlayed(id) { return played.has(id) || motionOff(); }
export function markPlayed(id) { played.add(id); }

/** Finish every animation on the page now, and skip the ones to come. */
export function skipAnimations() { skipAll = true; for (const f of finishers) f(); finishers.clear(); }
export function resumeAnimations() { skipAll = false; }

const finishers = new Set();

/** Resolves after `ms`, or at once on skip or when `signal` aborts. */
export const wait = (ms, signal) => new Promise((resolve) => {
  if (motionOff() || signal?.aborted) return resolve();
  const t = setTimeout(done, ms);
  signal?.addEventListener("abort", done);
  function done() { clearTimeout(t); finishers.delete(done); resolve(); }
  finishers.add(done);
});

/** Type `text` into `el` a character at a time, with a caret. */
export function typeInto(el, text, { cps = 110, signal } = {}) {
  return new Promise((resolve) => {
    if (motionOff() || signal?.aborted) { el.textContent = text; return resolve(); }
    const node = document.createTextNode("");
    const caret = document.createElement("span");
    caret.className = "caret";
    el.replaceChildren(node, caret);

    let i = 0, timer = null;
    const finish = () => {
      clearTimeout(timer);
      finishers.delete(finish);
      node.data = text;
      caret.remove();
      resolve();
    };
    finishers.add(finish);
    signal?.addEventListener("abort", finish);

    const tick = () => {
      // a few characters per frame reads as typing; long framings speed up so
      // no box takes much more than a couple of seconds
      const step = Math.max(1, Math.round(Math.max(cps, text.length / 2.2) / 60));
      i = Math.min(text.length, i + step);
      node.data = text.slice(0, i);
      if (i >= text.length) return finish();
      const ch = text[i - 1];
      const pause = /[.;:?!]/.test(ch) ? 120 : 1000 / 60;
      timer = setTimeout(tick, pause);
    };
    tick();
  });
}

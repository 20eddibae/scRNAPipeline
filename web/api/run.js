/* Optional: serve a live run record from a Modal web endpoint.
 *
 * The static page works with no backend at all. This function exists so a
 * deployed demo can show a run that just finished on Modal without the browser
 * hitting a cross-origin endpoint directly.
 *
 * Set MODAL_RUN_URL in the Vercel project's environment; leave it unset and the
 * function reports that cleanly rather than 500-ing. Load it in the page with
 *   ?api=/api/run
 * No key is read here: the endpoint on the Modal side is the trust boundary. */

export default async function handler(req, res) {
  const upstream = process.env.MODAL_RUN_URL;
  if (!upstream) {
    res.status(503).json({
      error: "MODAL_RUN_URL is not set on this deployment",
      hint: "the bundled record at /data/run-demo.json is served without it",
    });
    return;
  }

  try {
    const response = await fetch(upstream, { headers: { accept: "application/json" } });
    if (!response.ok) {
      res.status(502).json({ error: `upstream returned ${response.status}` });
      return;
    }
    res.setHeader("Cache-Control", "public, max-age=30");
    res.status(200).json(await response.json());
  } catch (err) {
    res.status(502).json({ error: String(err.message ?? err) });
  }
}

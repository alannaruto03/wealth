# wealth live view (static page)

A single self-contained page that shows the Polymarket bot's live stats
(equity, win rate, positions, resolutions) from anywhere. The bot on your PC
publishes a JSON snapshot to a secret GitHub gist (`publish: true` in
`configs/polymarket.yaml` + `WEALTH_PUBLISH_TOKEN`); this page fetches it.

Deploy anywhere that serves static files. **Vercel:** Add New… → Project →
Import this repo → set **Root Directory** to `web` → Deploy (no build step).
Then open:

```
https://<your-project>.vercel.app/?gist=<gist-id>
```

The gist id is printed by `wealth polymarket run` / `wealth polymarket publish`
and remembered by the page after the first visit. Anyone with the link can
view (read-only); delete the gist to rotate access.

GitHub Pages works identically (serve the `web/` folder).

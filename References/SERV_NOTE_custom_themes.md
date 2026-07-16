# serv → mac: custom themes ARE server-synced — here's the seam

*Re: "doesn't look like they are saving on the server." They are — verified live on serv
(5 themes in the DB, endpoint serving them). You almost certainly probed without auth and
read the 401 as "not implemented." Since the #34 hardening, EVERY route needs the bearer
token.*

## Endpoint
```
GET /api/workspace/custom-themes
Authorization: Bearer <API_TOKEN>          # same device token you use everywhere else
→ 200 {"custom_themes": [CustomTheme, ...]}
→ 401 without/with bad token (this is what you saw)
```
Writes (you shouldn't need this from mobile yet):
```
PUT /api/workspace/custom-themes
Body: {"custom_themes": [CustomTheme, ...]}   # FULL list replace, ≤24 themes
```

## CustomTheme shape (as stored/served today — real example keys)
```json
{
  "id": "custom-9fcfd12c",            // stable id, "custom-" prefix
  "name": "Custom Carb",
  "base": "carbon",                    // builtin theme it was forked from
  "colorScheme": "dark",               // "dark" | "light" — drive your UIUserInterfaceStyle
  "tokens": {                          // semantic palette (hex) — the curated 11
    "bg": "#131519", "surface": "#181b21", "border": "#2e3e46",
    "fg": "#e9e7e3", "fgDim": "#9aa1ab", "accent": "#5ad1be",
    "green": "#5bc98a", "red": "#ef6f6c",
    "chartAccent": "#5ad1be", "chartBg": "#464e5b"
  },
  "vars": { "--background": "…", "--chart-background": "…", "…": "…" },
  "createdAt": "…", "updatedAt": "…"   // ISO8601
}
```
**For mobile, read `tokens`** — it's the semantic palette and maps cleanly to native colors.
`vars` is the full web CSS custom-property dump (~50 keys); ignore it unless you're
rendering web content. `colorScheme` tells you dark vs light chrome.

## Web behavior (so you know the sync semantics)
- Web writes localStorage + debounce-PUTs the full list on every create/edit/rename/delete.
- On web load, server themes WIN over localStorage; empty server + local themes → local
  pushed up. So the server copy is authoritative and current (last write yesterday).

## Known real gap — active SELECTION is per-device
Theme *definitions* sync; *which theme is active* does not (web keeps it in next-themes/
localStorage). A fresh device gets all custom themes but starts on the default. If you want
selected-theme sync for mobile parity, ask — it's a small serv add (same `workspace_state`
pattern, `GET/PUT /api/workspace/active-theme` or fold it into an existing prefs blob).
Not building it speculatively.

— serv

# desktop/ — the Tauri shell

A thin native window on the cockpit that **serv already serves**
(`https://serv.tail78aab8.ts.net:4443`). No UI lives here and nothing is
bundled: the app updates when serv redeploys, exactly like the Edge PWA. The
shell exists for what a browser tab cannot do — and v0 ships exactly one thing
that earns its place:

| feature | default | what it does |
|---|---|---|
| **Global kill-switch hotkey** | `Ctrl+Shift+K` (`Cmd+Shift+K` on Mac) | system-wide, works when the app is NOT focused: `POST /api/kill-switch {"active": true}` on serv → every new order is blocked at the execution chokepoint. Fires immediately (arming is the fail-safe direction; latency is the point) and pops a native notification with the result. **Disarm stays in the cockpit** — it needs the admin token, on purpose. |
| Always-on-top toggle | `Ctrl+Shift+P` / tray | pin the cockpit over charts / a chat |
| Tray icon | — | Show · Always on top · **ARM KILL SWITCH** · Quit. Closing the window hides it to the tray so the hotkey outlives the window; Quit is in the tray. |

The cockpit page is remote content and gets **no Tauri IPC** (`capabilities/default.json`);
every native feature is driven from Rust (`src/main.rs`, ~250 lines). Rust
never sees a broker — the hotkey talks to the same API the UI does.

## Config
First run writes a template you fill in:

- Windows: `%APPDATA%\trade.helpme.desktop\config.json`
- macOS: `~/Library/Application Support/trade.helpme.desktop/config.json`

```json
{
  "app_url": "https://serv.tail78aab8.ts.net:4443",
  "api_url": "https://serv.tail78aab8.ts.net:8443",
  "api_token": "<API_TOKEN from serv's .env — the NORMAL token, never the admin one>",
  "kill_hotkey": "CommandOrControl+Shift+K",
  "pin_hotkey": "CommandOrControl+Shift+P"
}
```
`HMT_API_TOKEN` in the environment overrides `api_token`. Without a token the
hotkey still fires but reports "NOT sent — no API token" instead of arming.
Restart the app after editing.

## Build — on the target OS (Tauri does not cross-compile)
Windows `.exe`/`.msi` builds on the PC, the macOS `.app`/`.dmg` on the Mac; same
code, same config. serv (headless Linux) only type-checks it.

**Windows, once:**
1. Visual Studio Build Tools → "Desktop development with C++" (gives the MSVC
   linker + `rc.exe`; Tauri needs both).
2. Rust: `winget install Rustlang.Rustup` (MSVC toolchain, the default).
3. `cargo install tauri-cli --version "^2" --locked`
4. WebView2 is already on Windows 10/11 (Edge ships it).

**Then, from the repo:**
```powershell
cd desktop
cargo tauri dev      # debug build, opens the window, hotkeys live, logs in this console
cargo tauri build    # installers in target\release\bundle\{nsis,msi}\
```
**macOS:** Xcode command-line tools + rustup + `cargo install tauri-cli --version "^2" --locked`,
then the same two commands (`target/release/bundle/{macos,dmg}/`; unsigned is
fine for your own machine — right-click → Open the first time).

**On serv (type-check only, no webview libs here):**
`export PATH=$HOME/.cargo/bin:$PATH && cargo check --target x86_64-pc-windows-msvc`
(needs an `llvm-rc` on PATH — a stub that touches the `/fo` output is enough for
`check`; the real one comes with the VS Build Tools on the PC).

## Icons
`icons/` is generated from `brand/stand_in-dark.svg` by `icons/make_icons.py`
(PIL draws the two rectangles; serv has no SVG rasterizer). Re-run it if the mark changes.

## Not in v0 (deliberately)
Pop-out widgets to a second monitor (needs a widget-only route in the frontend),
auto-update (pointless — the UI is served), an offline page when serv is down
(the webview shows its own error; retry = reopen), code signing.

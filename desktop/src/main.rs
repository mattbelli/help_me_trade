//! help_me_trade desktop shell.
//!
//! A thin Tauri window on the cockpit that serv already serves — no UI lives
//! here, nothing is bundled, the app updates when serv redeploys. The shell
//! exists for what a browser tab cannot do:
//!   * a GLOBAL kill-switch hotkey (fires even when the app is not focused),
//!   * always-on-top toggle,
//!   * a tray icon that keeps the hotkey alive when the window is closed.
//!
//! The cockpit page is remote content and gets NO Tauri IPC (see
//! `capabilities/default.json`); every native feature is driven from Rust.
//! Config: `<app-config-dir>/config.json` (written as a template on first
//! run) — the API token goes there or in `HMT_API_TOKEN`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{fs, path::PathBuf, time::Duration};

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_notification::NotificationExt;

const DEFAULT_APP_URL: &str = "https://serv.tail78aab8.ts.net:4443";
const DEFAULT_API_URL: &str = "https://serv.tail78aab8.ts.net:8443";
const DEFAULT_KILL_HOTKEY: &str = "CommandOrControl+Shift+K";
const DEFAULT_PIN_HOTKEY: &str = "CommandOrControl+Shift+P";
const HTTP_TIMEOUT: Duration = Duration::from_secs(6);

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct Config {
    /// The cockpit (Next frontend) — what the window loads.
    app_url: String,
    /// The FastAPI backend — where the kill switch is posted.
    api_url: String,
    /// `API_TOKEN` from serv's `.env` (normal token: enough to ARM, never to disarm).
    api_token: String,
    /// Global hotkeys, tauri-plugin-global-shortcut syntax.
    kill_hotkey: String,
    pin_hotkey: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            app_url: DEFAULT_APP_URL.into(),
            api_url: DEFAULT_API_URL.into(),
            api_token: String::new(),
            kill_hotkey: DEFAULT_KILL_HOTKEY.into(),
            pin_hotkey: DEFAULT_PIN_HOTKEY.into(),
        }
    }
}

/// Managed state: config + one HTTP client + the tray check item we keep in sync.
struct Shell {
    cfg: Config,
    cfg_path: Option<PathBuf>,
    http: reqwest::Client,
    pin_item: CheckMenuItem<Wry>,
}

fn load_config(app: &AppHandle) -> (Config, Option<PathBuf>) {
    let path = app.path().app_config_dir().ok().map(|d| d.join("config.json"));
    let mut cfg = match path.as_ref().and_then(|p| fs::read_to_string(p).ok()) {
        Some(text) => serde_json::from_str::<Config>(&text).unwrap_or_else(|e| {
            eprintln!("config.json unreadable ({e}) — using defaults");
            Config::default()
        }),
        None => {
            // First run: write a template so the token has an obvious place to go.
            let cfg = Config::default();
            if let Some(p) = &path {
                if let Some(dir) = p.parent() {
                    let _ = fs::create_dir_all(dir);
                }
                let _ = fs::write(p, serde_json::to_string_pretty(&cfg).unwrap_or_default());
                eprintln!("wrote config template to {}", p.display());
            }
            cfg
        }
    };
    if let Ok(t) = std::env::var("HMT_API_TOKEN") {
        if !t.trim().is_empty() {
            cfg.api_token = t.trim().to_string();
        }
    }
    (cfg, path)
}

fn notify(app: &AppHandle, title: &str, body: &str) {
    eprintln!("{title}: {body}");
    let _ = app.notification().builder().title(title).body(body).show();
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn toggle_pin(app: &AppHandle) {
    let Some(w) = app.get_webview_window("main") else { return };
    let on = !w.is_always_on_top().unwrap_or(false);
    let _ = w.set_always_on_top(on);
    let _ = w.set_title(if on { "help_me_trade — pinned" } else { "help_me_trade" });
    let _ = app.state::<Shell>().pin_item.set_checked(on);
    if on {
        show_main(app);
    }
}

/// ARM the kill switch: `POST {api_url}/api/kill-switch {"active": true}`.
/// Arming is the fail-safe direction (blocks every new order at the execution
/// chokepoint) so it fires immediately, no confirm — latency is the point.
/// Disarming needs the admin token and stays in the cockpit on purpose.
fn arm_kill_switch(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if shell.cfg.api_token.is_empty() {
        let where_ = shell
            .cfg_path
            .as_ref()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "config.json".into());
        notify(
            app,
            "Kill switch NOT sent — no API token",
            &format!("put serv's API_TOKEN in {where_} (api_token) or HMT_API_TOKEN, then restart"),
        );
        show_main(app);
        return;
    }
    let http = shell.http.clone();
    let url = format!("{}/api/kill-switch", shell.cfg.api_url.trim_end_matches('/'));
    let token = shell.cfg.api_token.clone();
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let res = http
            .post(&url)
            .header("X-API-Token", token)
            .json(&serde_json::json!({ "active": true }))
            .send()
            .await;
        match res {
            Ok(r) if r.status().is_success() => notify(
                &handle,
                "Kill switch ARMED",
                "serv is blocking all new orders. Disarm from the cockpit (admin token).",
            ),
            Ok(r) => {
                let status = r.status();
                let body = r.text().await.unwrap_or_default();
                notify(&handle, "Kill switch FAILED", &format!("HTTP {status} — {body}"));
            }
            Err(e) => notify(&handle, "Kill switch FAILED", &format!("could not reach serv: {e}")),
        }
    });
    show_main(app);
}

fn parse_hotkey(name: &str, raw: &str) -> Result<Shortcut, String> {
    raw.parse::<Shortcut>()
        .map_err(|e| format!("{name} = {raw:?} is not a valid hotkey ({e}); example: {DEFAULT_KILL_HOTKEY}"))
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            let handle = app.handle().clone();
            let (cfg, cfg_path) = load_config(&handle);
            let kill_hotkey = parse_hotkey("kill_hotkey", &cfg.kill_hotkey)?;
            let pin_hotkey = parse_hotkey("pin_hotkey", &cfg.pin_hotkey)?;
            let app_url = url::Url::parse(&cfg.app_url)
                .map_err(|e| format!("app_url {:?} is not a URL ({e})", cfg.app_url))?;

            // The window: the cockpit, remote. Close hides to the tray so the
            // global hotkey outlives the window; Quit lives in the tray menu.
            let win = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(app_url))
                .title("help_me_trade")
                .inner_size(1440.0, 900.0)
                .min_inner_size(900.0, 600.0)
                .build()?;
            let h = handle.clone();
            win.on_window_event(move |ev| {
                if let WindowEvent::CloseRequested { api, .. } = ev {
                    api.prevent_close();
                    if let Some(w) = h.get_webview_window("main") {
                        let _ = w.hide();
                    }
                }
            });

            // Tray: show / pin / ARM / quit.
            let show = MenuItem::with_id(app, "show", "Show cockpit", true, None::<&str>)?;
            let pin = CheckMenuItem::with_id(
                app,
                "pin",
                format!("Always on top  ({})", cfg.pin_hotkey),
                true,
                false,
                None::<&str>,
            )?;
            let kill = MenuItem::with_id(
                app,
                "kill",
                format!("ARM KILL SWITCH  ({})", cfg.kill_hotkey),
                true,
                None::<&str>,
            )?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &show,
                    &pin,
                    &PredefinedMenuItem::separator(app)?,
                    &kill,
                    &PredefinedMenuItem::separator(app)?,
                    &quit,
                ],
            )?;
            let icon = app
                .default_window_icon()
                .cloned()
                .ok_or("bundle.icon is empty — no tray icon")?;
            TrayIconBuilder::with_id("main")
                .icon(icon)
                .tooltip("help_me_trade")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, ev| match ev.id().as_ref() {
                    "show" => show_main(app),
                    "pin" => toggle_pin(app),
                    "kill" => arm_kill_switch(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, ev| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = ev
                    {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            let http = reqwest::Client::builder()
                .timeout(HTTP_TIMEOUT)
                .user_agent("hmt-desktop/0.1")
                .build()?;
            app.manage(Shell { cfg: cfg.clone(), cfg_path, http, pin_item: pin });

            // Global hotkeys — the reason this shell exists.
            let gs = app.global_shortcut();
            gs.on_shortcut(kill_hotkey, |app, _sc, ev| {
                if ev.state() == ShortcutState::Pressed {
                    arm_kill_switch(app);
                }
            })?;
            gs.on_shortcut(pin_hotkey, |app, _sc, ev| {
                if ev.state() == ShortcutState::Pressed {
                    toggle_pin(app);
                }
            })?;
            eprintln!(
                "help_me_trade desktop: {} | kill = {} | pin = {} | token {}",
                cfg.app_url,
                cfg.kill_hotkey,
                cfg.pin_hotkey,
                if cfg.api_token.is_empty() { "MISSING" } else { "set" }
            );
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("help_me_trade desktop failed to start");
}

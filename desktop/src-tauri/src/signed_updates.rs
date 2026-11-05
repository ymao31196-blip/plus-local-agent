//! Signed Windows updater, kept behind a compile-time public-key gate.
//! No private signing material or arbitrary endpoint is accepted from the UI.
use serde_json::{json, Value};
use std::sync::{atomic::{AtomicBool, Ordering}, Mutex};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::Backend;

static INSTALLING: AtomicBool = AtomicBool::new(false);
const STABLE_ENDPOINT: &str =
    "https://github.com/ymao31196-blip/plus-local-agent/releases/latest/download/latest.json";

pub struct PendingUpdate(pub Mutex<Option<Update>>);

pub fn is_installing() -> bool {
    INSTALLING.load(Ordering::Acquire)
}

pub fn register_updater(app: &AppHandle) -> tauri::Result<()> {
    // An unsigned local build never exposes install capability.
    if let Some(pubkey) = option_env!("PLA_UPDATER_PUBLIC_KEY") {
        if !pubkey.trim().is_empty() {
            app.plugin(tauri_plugin_updater::Builder::new()
                .pubkey(pubkey)
                .build())?;
        }
    }
    Ok(())
}

pub fn signed_updates_enabled() -> bool {
    option_env!("PLA_UPDATER_PUBLIC_KEY").is_some_and(|key| !key.trim().is_empty())
}

#[tauri::command]
pub async fn signed_update_check(app: AppHandle, pending: State<'_, PendingUpdate>) -> Result<Value, String> {
    if !signed_updates_enabled() {
        return Ok(json!({"status": "unconfigured", "installable": false}));
    }
    if is_installing() {
        return Err("Update installation already started".into());
    }
    // A fixed HTTPS endpoint avoids trusting URLs controlled by the renderer.
    // No payload is executed unless the Tauri updater verifies its signature.
    let update = app.updater_builder()
        .endpoints(vec![STABLE_ENDPOINT.parse().map_err(|_| "Invalid update endpoint")?])
        .map_err(|e| e.to_string())?
        .timeout(std::time::Duration::from_secs(15))
        .build().map_err(|e| e.to_string())?
        .check().await.map_err(|e| e.to_string())?;

    let metadata = match update.as_ref() {
        Some(item) => json!({"status": "available", "installable": true,
                            "version": item.version, "current_version": item.current_version}),
        None => json!({"status": "latest", "installable": false}),
    };
    *pending.0.lock().map_err(|_| "Update lock unavailable")? = update;
    Ok(metadata)
}

struct InstallReservation;
impl InstallReservation {
    fn reserve() -> Result<Self, String> {
        INSTALLING.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "Update installation already started".to_string())?;
        Ok(Self)
    }
}
impl Drop for InstallReservation {
    fn drop(&mut self) {
        INSTALLING.store(false, Ordering::Release);
    }
}

#[tauri::command]
pub async fn signed_update_install(
    pending: State<'_, PendingUpdate>,
    backend: State<'_, Backend>,
) -> Result<(), String> {
    if !signed_updates_enabled() {
        return Err("Signed updates are unavailable in this build".into());
    }
    let _reservation = InstallReservation::reserve()?;
    // Guard both before the download and again immediately before installation:
    // caller must stop Runtime, Tunnel, agent pet, component installers and
    // background workers through the ordinary Desktop UI first.
    let ready = backend.0.lock().map_err(|_| "Manager lock unavailable")?
        .call("update_preflight", json!({}))?;
    if ready["ready"] != true {
        return Err("请先停止 PLA Runtime、Tunnel 和正在执行的任务，再安装更新".into());
    }
    let update = pending.0.lock().map_err(|_| "Update lock unavailable")?
        .take().ok_or("No verified update metadata is available; check again")?;
    let bytes = update.download(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    // Tauri checks the downloaded bytes against its embedded public key during
    // install; reject if any service was restarted while downloading.
    let ready = backend.0.lock().map_err(|_| "Manager lock unavailable")?
        .call("update_preflight", json!({}))?;
    if ready["ready"] != true {
        return Err("下载已完成，但发现服务重新启动；已取消安装，请重新检查更新".into());
    }
    // Windows exits the app automatically after launching the installer.
    update.install(bytes).map_err(|e| e.to_string())?;
    Ok(())
}

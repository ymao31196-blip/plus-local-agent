#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::{io::{BufRead, BufReader, Write}, process::{Child, ChildStdin, Command, Stdio}, sync::{Mutex, mpsc}, time::Duration};
use std::os::windows::{io::AsRawHandle, process::CommandExt};
use tauri::{Manager, State, menu::{Menu, MenuItem}, tray::{TrayIconBuilder, TrayIconEvent, MouseButton, MouseButtonState}};
use windows_sys::Win32::{Foundation::{CloseHandle, HANDLE}, System::JobObjects::*};

struct Bridge {
    child: Child,
    input: ChildStdin,
    output: mpsc::Receiver<String>,
    job: HANDLE,
    broken: bool,
}
unsafe impl Send for Bridge {}

impl Bridge {
    fn call(&mut self, command: &str, args: Value) -> Result<Value, String> {
        if self.broken { return Err("Manager unavailable; reopen PLA Desktop to recover".into()); }
        let message = json!({"command":command,"args":args}).to_string();
        if message.len() > 65536 { return Err("Request too large".into()); }
        writeln!(self.input, "{message}").map_err(|_| "Manager input closed")?;
        self.input.flush().map_err(|_| "Manager input closed")?;
        let response = match self.output.recv_timeout(Duration::from_secs(100)) {
            Ok(response) => response,
            Err(_) => { self.broken=true; unsafe { TerminateJobObject(self.job,1); } return Err("Manager stopped responding; reopen PLA Desktop to recover".into()); }
        };
        let value: Value = serde_json::from_str(&response).map_err(|_| "Manager failed; reopen PLA Desktop to recover")?;
        if value["ok"] == true { Ok(value["result"].clone()) }
        else { Err(value["error"].as_str().unwrap_or("Management failed").to_string()) }
    }
}
impl Drop for Bridge {
    fn drop(&mut self) {
        // Kill-on-close includes Runtime, runner, Tunnel and every inherited child.
        unsafe { CloseHandle(self.job); }
        let _ = self.child.wait();
    }
}
struct Backend(Mutex<Bridge>);

#[tauri::command]
fn quit_app(app: tauri::AppHandle) { app.exit(0); }

#[tauri::command]
async fn manage(command: String, args: Value, state: State<'_, Backend>) -> Result<Value,String> {
    const ALLOWED: &[&str] = &["status","start","stop","restart","connect","configure","credential","workspace_save","workspace_remove","verify","diagnose","logs","detect_legacy","provider_catalog","provider_import","provider_details","provider_action","skill_action","provider_install","installation_status","skill_permissions","development_status","development_prepare","development_configure"];
    if !ALLOWED.contains(&command.as_str()) { return Err("Unsupported management command".into()); }
    state.0.lock().map_err(|_| "Manager lock unavailable")?.call(&command,args)
}

#[tauri::command]
fn autostart(enabled: bool, app: tauri::AppHandle, state: State<'_, Backend>) -> Result<(),String> {
    use winreg::{RegKey, enums::HKEY_CURRENT_USER};
    let key = RegKey::predef(HKEY_CURRENT_USER).create_subkey("Software\\Microsoft\\Windows\\CurrentVersion\\Run").map_err(|e| e.to_string())?.0;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let own_registration = format!("\"{}\"", executable.display());
    let previous: Option<String> = match key.get_value("PLADesktop") { Ok(value)=>Some(value), Err(error) if error.kind()==std::io::ErrorKind::NotFound=>None, Err(error)=>return Err(error.to_string()) };
    if previous.as_ref().is_some_and(|value| !value.eq_ignore_ascii_case(&own_registration)) {
        return Err("A different PLA Desktop startup registration exists; it was preserved".into());
    }
    if enabled {
        key.set_value("PLADesktop", &own_registration).map_err(|e|e.to_string())?;
    } else { match key.delete_value("PLADesktop") { Ok(_) => (), Err(e) if e.kind() == std::io::ErrorKind::NotFound => (), Err(e) => return Err(e.to_string()) } }
    let _ = app;
    if let Err(error)=state.0.lock().map_err(|_| "Manager lock unavailable")?.call("configure", json!({"autostart":enabled})) {
        let restored=if let Some(value)=previous {key.set_value("PLADesktop",&value)} else {match key.delete_value("PLADesktop") {Ok(())=>Ok(()),Err(e) if e.kind()==std::io::ErrorKind::NotFound=>Ok(()),Err(e)=>Err(e)}};
        return Err(if restored.is_ok(){error}else{format!("{error}; could not restore Windows startup registration")});
    }
    Ok(())
}

#[tauri::command]
fn official_link(topic: String) -> Result<(), String> {
    let url = match topic.as_str() {
        "tunnels" => "https://platform.openai.com/settings/organization/tunnels",
        "keys" => "https://platform.openai.com/api-keys",
        "guide" => "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels",
        _ => return Err("Unknown help topic".into())
    };
    // Fixed official URLs; no user-controlled arguments or general Shell interface.
    let system_root = std::env::var_os("SystemRoot").ok_or("Windows system directory is unavailable")?;
    Command::new(std::path::PathBuf::from(system_root).join("System32/rundll32.exe")).args(["url.dll,FileProtocolHandler",url]).creation_flags(0x08000000).spawn().map_err(|e|e.to_string())?;
    Ok(())
}

fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") { let _=window.show(); let _=window.unminimize(); let _=window.set_focus(); }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .invoke_handler(tauri::generate_handler![manage, autostart, official_link, quit_app])
        .setup(|app| {
            let resources = app.path().resource_dir()?.join("resources");
            let arguments: Vec<String> = std::env::args().collect();
            let data = if let Some(position) = arguments.iter().position(|arg| arg == "--data-dir") {
                std::path::PathBuf::from(arguments.get(position+1).ok_or("--data-dir requires a path")?)
            } else { app.path().app_local_data_dir()? };
            std::fs::create_dir_all(&data)?;
            let mut child = Command::new(resources.join("runtime/pla-runtime.exe"))
                .args(["manager","--data-dir"]).arg(&data).arg("--resources").arg(&resources)
                .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).creation_flags(0x08000000).spawn()?;
            let job = unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() { return Err(std::io::Error::last_os_error().into()); }
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(handle, JobObjectExtendedLimitInformation, &limits as *const _ as _, std::mem::size_of_val(&limits) as u32)==0 || AssignProcessToJobObject(handle,child.as_raw_handle() as HANDLE)==0 {
                    let error=std::io::Error::last_os_error(); let _=child.kill(); CloseHandle(handle); return Err(error.into());
                }
                handle
            };
            let input=child.stdin.take().unwrap(); let reader=BufReader::new(child.stdout.take().unwrap());
            let (sender,output)=mpsc::sync_channel(1);
            std::thread::spawn(move || { for line in reader.lines() { match line { Ok(line) if line.len() <= 4_000_000 => { if sender.send(line).is_err() {break;} }, _=>break } } });
            app.manage(Backend(Mutex::new(Bridge{child,input,output,job,broken:false})));
            let open=MenuItem::with_id(app,"open","打开管理界面 / Open",true,None::<&str>)?;
            let start=MenuItem::with_id(app,"start","启动 Runtime / Start",true,None::<&str>)?;
            let stop=MenuItem::with_id(app,"stop","停止 Runtime / Stop",true,None::<&str>)?;
            let status=MenuItem::with_id(app,"status","查看状态 / Status",true,None::<&str>)?;
            let quit=MenuItem::with_id(app,"quit","彻底退出 / Quit",true,None::<&str>)?;
            let menu=Menu::with_items(app,&[&open,&status,&start,&stop,&quit])?;
            let mut tray=TrayIconBuilder::new().menu(&menu).tooltip("PLA Desktop — ChatGPT is the interface")
                .on_menu_event(|app,event| match event.id.as_ref() {
                    "open"|"status" => show(app),
                    "start"|"stop" => { let app=app.clone(); let command=event.id.as_ref().to_owned(); tauri::async_runtime::spawn(async move { let state=app.state::<Backend>(); let result=state.0.lock().unwrap().call(&command,json!({})); if result.is_err() { show(&app); } }); },
                    "quit" => app.exit(0), _=>()
                }).on_tray_icon_event(|tray,event| if let TrayIconEvent::Click{button:MouseButton::Left,button_state:MouseButtonState::Up,..}=event {show(tray.app_handle());});
            if let Some(icon)=app.default_window_icon() {tray=tray.icon(icon.clone());}
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window,event| if let tauri::WindowEvent::CloseRequested{api,..}=event {api.prevent_close();let _=window.hide();})
        .build(tauri::generate_context!()).expect("Failed to initialize PLA Desktop")
        .run(|app,event| if let tauri::RunEvent::Exit=event { let state=app.state::<Backend>(); if let Ok(mut bridge)=state.0.try_lock() { let _=bridge.call("stop",json!({})); }; });
}

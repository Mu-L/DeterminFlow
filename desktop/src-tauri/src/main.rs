#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod updater;

use std::sync::Arc;

use backend::{BackendState, LaunchedBackend};
use tauri::{Manager, RunEvent};

fn show_startup_error(window: &tauri::WebviewWindow, message: &str) {
    let encoded = serde_json::to_string(message).unwrap_or_else(|_| "\"未知启动错误\"".to_string());
    let _ = window.eval(format!("window.showBackendError({encoded})"));
}

fn navigate_when_ready(window: tauri::WebviewWindow, url: String) {
    tauri::async_runtime::spawn(async move {
        let health_url = url.clone();
        let outcome =
            tauri::async_runtime::spawn_blocking(move || backend::wait_until_ready(&health_url))
                .await;

        match outcome {
            Ok(Ok(())) => match url.parse() {
                Ok(parsed) => {
                    if let Err(error) = window.navigate(parsed) {
                        show_startup_error(&window, &format!("无法打开本地界面: {error}"));
                    }
                }
                Err(error) => show_startup_error(&window, &format!("本地地址无效: {error}")),
            },
            Ok(Err(error)) => show_startup_error(&window, &error),
            Err(error) => show_startup_error(&window, &format!("启动检查异常: {error}")),
        }
    });
}

#[tauri::command]
fn prepare_for_update(backend_state: tauri::State<'_, Arc<BackendState>>) {
    backend_state.stop();
}

fn main() {
    let backend_state = Arc::new(BackendState::new());
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _cwd| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ))
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(backend_state)
        .invoke_handler(tauri::generate_handler![
            prepare_for_update,
            updater::check_update_sources,
        ])
        .setup(|app| {
            let window = app.get_webview_window("main").ok_or("无法创建主窗口")?;
            let LaunchedBackend { child, url } = backend::launch(app.handle())?;
            app.state::<Arc<BackendState>>().track(child)?;
            navigate_when_ready(window, url);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("DeterminFlow desktop initialization failed");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            app_handle.state::<Arc<BackendState>>().stop();
        }
    });
}

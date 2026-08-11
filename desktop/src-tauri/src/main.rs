#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod onboarding;
mod updater;

use std::sync::Arc;

use backend::{BackendState, LaunchedBackend};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

fn is_allowed_external_url(url: &tauri::Url) -> bool {
    let has_safe_authority =
        url.host_str().is_some() && url.username().is_empty() && url.password().is_none();
    match url.scheme() {
        "https" => has_safe_authority,
        "http" => {
            has_safe_authority
                && matches!(
                    url.host_str(),
                    Some("localhost" | "127.0.0.1" | "::1" | "[::1]")
                )
        }
        _ => false,
    }
}

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
            onboarding::get_desktop_onboarding_status,
            onboarding::set_desktop_onboarding_status,
            updater::check_update_sources,
        ])
        .setup(|app| {
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("DeterminFlow")
                    .inner_size(1440.0, 920.0)
                    .min_inner_size(1024.0, 700.0)
                    .resizable(true)
                    .center()
                    .initialization_script(include_str!("../../ui/desktop-adapter.js"))
                    .on_new_window(|url, _features| {
                        if is_allowed_external_url(&url) {
                            let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
                        }
                        tauri::webview::NewWindowResponse::Deny
                    })
                    .build()?;
            match backend::launch(app.handle()) {
                Ok(LaunchedBackend { child, url }) => {
                    app.state::<Arc<BackendState>>().track(child)?;
                    navigate_when_ready(window, url);
                }
                Err(error) => show_startup_error(&window, &error),
            }
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

#[cfg(test)]
mod tests {
    use super::is_allowed_external_url;

    fn url(value: &str) -> tauri::Url {
        value.parse().expect("test URL should parse")
    }

    #[test]
    fn external_links_allow_https_and_development_loopback_http() {
        assert!(is_allowed_external_url(&url("https://bishuxiezuo.cn/")));
        assert!(is_allowed_external_url(&url(
            "http://127.0.0.1:5173/site/public-api-top-up.html"
        )));
        assert!(is_allowed_external_url(&url("http://localhost:5173/")));
        assert!(is_allowed_external_url(&url("http://[::1]:5173/")));
    }

    #[test]
    fn external_links_reject_insecure_remote_and_unsafe_schemes() {
        assert!(!is_allowed_external_url(&url("http://example.com/")));
        assert!(!is_allowed_external_url(&url(
            "https://user:password@example.com/"
        )));
        assert!(!is_allowed_external_url(&url("file:///tmp/example")));
        assert!(!is_allowed_external_url(&url("javascript:alert(1)")));
    }
}

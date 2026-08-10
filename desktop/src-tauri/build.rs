fn main() {
    let manifest = tauri_build::AppManifest::new().commands(&[
        "check_update_sources",
        "get_desktop_onboarding_status",
        "prepare_for_update",
        "set_desktop_onboarding_status",
    ]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("failed to build DeterminFlow desktop shell");
}

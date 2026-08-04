use std::time::{Duration, Instant};

use futures_util::future::join;
use semver::Version;
use serde::{Deserialize, Serialize};
use tauri::{Manager, ResourceId, Runtime, Webview};
use tauri_plugin_updater::{Update, UpdaterExt};
use url::Url;

const GITHUB_UPDATE_ENDPOINT: &str =
    "https://github.com/alikon-art/DeterminFlow/releases/latest/download/latest.json";
const GITEE_LATEST_RELEASE_API: &str =
    "https://gitee.com/api/v5/repos/alikon/DeterminFlow/releases/latest";
const UPDATE_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Deserialize)]
struct GiteeAsset {
    name: String,
    browser_download_url: String,
}

#[derive(Deserialize)]
struct GiteeRelease {
    assets: Vec<GiteeAsset>,
}

struct TimedUpdate {
    update: Option<Update>,
    elapsed: Duration,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateMetadata {
    rid: ResourceId,
    current_version: String,
    version: String,
    date: Option<String>,
    body: Option<String>,
    raw_json: serde_json::Value,
}

async fn gitee_update_endpoint() -> Result<Url, String> {
    let client = reqwest::Client::builder()
        .timeout(UPDATE_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    let release = client
        .get(GITEE_LATEST_RELEASE_API)
        .header(reqwest::header::USER_AGENT, "DeterminFlow-Updater")
        .send()
        .await
        .map_err(|error| error.to_string())?
        .error_for_status()
        .map_err(|error| error.to_string())?
        .json::<GiteeRelease>()
        .await
        .map_err(|error| error.to_string())?;
    let asset = release
        .assets
        .into_iter()
        .find(|asset| asset.name == "latest.json")
        .ok_or_else(|| "Gitee 最新发行版缺少 latest.json".to_string())?;
    Url::parse(&asset.browser_download_url).map_err(|error| error.to_string())
}

async fn check_endpoint<R: Runtime>(
    webview: Webview<R>,
    endpoint: Result<Url, String>,
    started: Instant,
) -> Result<TimedUpdate, String> {
    let endpoint = endpoint?;
    if endpoint.scheme() != "https" {
        return Err("更新地址必须使用 HTTPS".to_string());
    }
    let updater = webview
        .updater_builder()
        .endpoints(vec![endpoint])
        .map_err(|error| error.to_string())?
        .timeout(UPDATE_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    let update = updater.check().await.map_err(|error| error.to_string())?;
    Ok(TimedUpdate {
        update,
        elapsed: started.elapsed(),
    })
}

fn choose_update(
    github: Result<TimedUpdate, String>,
    gitee: Result<TimedUpdate, String>,
) -> Result<Option<Update>, String> {
    let (github, gitee) = match (github, gitee) {
        (Ok(github), Ok(gitee)) => (github, gitee),
        (Ok(available), Err(_)) | (Err(_), Ok(available)) => {
            return Ok(available.update);
        }
        (Err(github_error), Err(gitee_error)) => {
            return Err(format!(
                "GitHub 与 Gitee 更新源均不可用: {github_error}; {gitee_error}"
            ));
        }
    };

    match (github.update, gitee.update) {
        (None, None) => Ok(None),
        (Some(update), None) | (None, Some(update)) => Ok(Some(update)),
        (Some(github_update), Some(gitee_update)) => {
            let github_version =
                Version::parse(&github_update.version).map_err(|error| error.to_string())?;
            let gitee_version =
                Version::parse(&gitee_update.version).map_err(|error| error.to_string())?;
            if github_version != gitee_version {
                return Ok(Some(if github_version > gitee_version {
                    github_update
                } else {
                    gitee_update
                }));
            }
            Ok(Some(if github.elapsed <= gitee.elapsed {
                github_update
            } else {
                gitee_update
            }))
        }
    }
}

#[tauri::command]
pub async fn check_update_sources<R: Runtime>(
    webview: Webview<R>,
) -> Result<Option<UpdateMetadata>, String> {
    let github_started = Instant::now();
    let github_url = Url::parse(GITHUB_UPDATE_ENDPOINT).map_err(|error| error.to_string());
    let github = check_endpoint(webview.clone(), github_url, github_started);

    let gitee_started = Instant::now();
    let gitee = async {
        let endpoint = gitee_update_endpoint().await;
        check_endpoint(webview.clone(), endpoint, gitee_started).await
    };
    let (github_result, gitee_result) = join(github, gitee).await;
    let update = choose_update(github_result, gitee_result)?;

    Ok(update.map(|update| UpdateMetadata {
        current_version: update.current_version.clone(),
        version: update.version.clone(),
        date: update.date.map(|date| date.to_string()),
        body: update.body.clone(),
        raw_json: update.raw_json.clone(),
        rid: webview.resources_table().add(update),
    }))
}

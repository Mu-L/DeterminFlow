param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [ValidateSet("core", "full")]
    [string]$Flavor = "core"
)

$ErrorActionPreference = "Stop"
$ProductName = "DeterminFlow"
$UserData = Join-Path $env:LOCALAPPDATA "io.determinflow.desktop"
$AppProcess = $null
$Uninstaller = $null
$Installed = $false
$BackendBaseUrl = $null
$BackendProcessId = $null
$SecondAppProcess = $null
$OrphanBackendProcess = $null

function Get-UninstallEntry {
    Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" |
        Where-Object { $_.DisplayName -eq $ProductName } |
        Select-Object -First 1
}

function Get-CommandExecutable([string]$CommandLine) {
    if ($CommandLine -match '^"([^"]+)"') {
        return $Matches[1]
    }
    return ($CommandLine -split " ", 2)[0]
}

function Get-FreeTcpPort {
    $Listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $Listener.Start()
    try {
        return ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
    }
    finally {
        $Listener.Stop()
    }
}

function Get-InstalledBackendProcesses([string]$BackendExecutable) {
    $ExpectedPath = [System.IO.Path]::GetFullPath($BackendExecutable)
    return @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.Name -eq "determinflow-backend.exe" -and
                $_.ExecutablePath -and
                [string]::Equals(
                    [System.IO.Path]::GetFullPath($_.ExecutablePath),
                    $ExpectedPath,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }
    )
}

function Wait-InstalledBackendExit([string]$BackendExecutable) {
    $Deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $Deadline) {
        if (@(Get-InstalledBackendProcesses $BackendExecutable).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    $Remaining = @(Get-InstalledBackendProcesses $BackendExecutable)
    throw "Installed backend processes did not exit: $($Remaining.ProcessId -join ', ')"
}

function Start-TestBackend([string]$BackendExecutable, [string]$UserData) {
    $Port = Get-FreeTcpPort
    $Arguments = "--port $Port --user-data-dir `"$UserData`""
    $Process = Start-Process `
        -FilePath $BackendExecutable `
        -ArgumentList $Arguments `
        -PassThru
    $Deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $Deadline) {
        if ($Process.HasExited) {
            throw "Standalone backend exited before becoming ready"
        }
        try {
            $Response = Invoke-WebRequest `
                -Uri "http://127.0.0.1:$Port/api/system/status" `
                -UseBasicParsing `
                -TimeoutSec 2
            if ($Response.StatusCode -eq 200) {
                return $Process
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Standalone backend did not become ready"
}

try {
    $InstallResult = Start-Process -FilePath $Installer -ArgumentList "/S" -Wait -PassThru
    if ($InstallResult.ExitCode -ne 0) {
        throw "NSIS installer exited with code $($InstallResult.ExitCode)"
    }
    $Installed = $true

    $Entry = Get-UninstallEntry
    if (-not $Entry) {
        throw "DeterminFlow uninstall registry entry was not created"
    }
    $Uninstaller = Get-CommandExecutable $Entry.UninstallString
    $InstallDirectory = Split-Path -Parent $Uninstaller
    $BackendExecutable = Join-Path $InstallDirectory "runtime\backend\determinflow-backend.exe"
    if (-not (Test-Path $BackendExecutable)) {
        throw "Installed DeterminFlow backend was not found"
    }
    $Application = Get-ChildItem -Path $InstallDirectory -Filter "*.exe" |
        Where-Object { $_.Name -notmatch "uninstall" } |
        Select-Object -First 1
    if (-not $Application) {
        throw "Installed DeterminFlow executable was not found"
    }

    $AppProcess = Start-Process -FilePath $Application.FullName -PassThru
    $Deadline = (Get-Date).AddSeconds(60)
    $Ready = $false
    while ((Get-Date) -lt $Deadline -and -not $Ready) {
        if ($AppProcess.HasExited) {
            throw "Installed DeterminFlow application exited before becoming ready"
        }
        $Backends = Get-CimInstance Win32_Process |
            Where-Object {
                $_.ParentProcessId -eq $AppProcess.Id -and
                $_.Name -eq "determinflow-backend.exe"
            }
        foreach ($Backend in $Backends) {
            $Listeners = Get-NetTCPConnection -State Listen -OwningProcess $Backend.ProcessId `
                -ErrorAction SilentlyContinue
            foreach ($Listener in $Listeners) {
                try {
                    $Response = Invoke-WebRequest `
                        -Uri "http://127.0.0.1:$($Listener.LocalPort)/api/system/status" `
                        -UseBasicParsing `
                        -TimeoutSec 2
                    if ($Response.StatusCode -eq 200) {
                        $Ready = $true
                        $BackendBaseUrl = "http://127.0.0.1:$($Listener.LocalPort)"
                        $BackendProcessId = $Backend.ProcessId
                        break
                    }
                }
                catch {
                    continue
                }
            }
            if ($Ready) { break }
        }
        if (-not $Ready) { Start-Sleep -Milliseconds 500 }
    }
    if (-not $Ready) {
        throw "Installed DeterminFlow backend did not become ready"
    }
    if (-not (Test-Path (Join-Path $UserData "config\models_config.json"))) {
        throw "Installed application did not create isolated user configuration"
    }
    $Plugins = (Invoke-RestMethod -Uri "$BackendBaseUrl/api/plugins" -TimeoutSec 5).plugins
    $Bishu = $Plugins | Where-Object { $_.id -eq "bishu-novel" } | Select-Object -First 1
    if ($Flavor -eq "full") {
        if (-not $Bishu) {
            throw "Full installer did not seed bishu-novel"
        }
        if (-not $Bishu.active_enabled -or -not $Bishu.desired_enabled) {
            throw "Full installer did not enable bishu-novel"
        }
        if ($Bishu.runtime_status -ne "running") {
            throw "Full installer Plugin is not running: $($Bishu.runtime_status)"
        }
    }
    elseif ($Bishu) {
        throw "Core installer unexpectedly seeded bishu-novel"
    }
    Write-Output "Installed application status endpoint verified"

    $SecondAppProcess = Start-Process -FilePath $Application.FullName -PassThru
    if (-not $SecondAppProcess.WaitForExit(10000)) {
        throw "Second DeterminFlow instance did not exit"
    }
    Start-Sleep -Milliseconds 500
    $BackendsAfterSecondLaunch = @(Get-InstalledBackendProcesses $BackendExecutable)
    if ($BackendsAfterSecondLaunch.Count -ne 1) {
        throw "Second launch created duplicate backends: $($BackendsAfterSecondLaunch.ProcessId -join ', ')"
    }
    if ($BackendsAfterSecondLaunch[0].ProcessId -ne $BackendProcessId) {
        throw "Second launch replaced the original backend unexpectedly"
    }
    Write-Output "Single-instance backend ownership verified"

    $AppProcess.Refresh()
    if (-not $AppProcess.CloseMainWindow()) {
        throw "DeterminFlow main window did not accept a normal close request"
    }
    if (-not $AppProcess.WaitForExit(15000)) {
        throw "DeterminFlow application did not exit after closing its main window"
    }
    Wait-InstalledBackendExit $BackendExecutable
    Write-Output "Normal application exit removed all installed backend processes"

    $OrphanBackendProcess = Start-TestBackend $BackendExecutable $UserData
    $ReinstallResult = Start-Process -FilePath $Installer -ArgumentList "/S" -Wait -PassThru
    if ($ReinstallResult.ExitCode -ne 0) {
        throw "NSIS reinstall with a stale backend exited with code $($ReinstallResult.ExitCode)"
    }
    Wait-InstalledBackendExit $BackendExecutable
    $OrphanBackendProcess.Refresh()
    if (-not $OrphanBackendProcess.HasExited) {
        throw "NSIS preinstall hook did not stop the stale backend"
    }
    Write-Output "NSIS reinstall recovered from a stale backend process"
}
finally {
    if ($SecondAppProcess -and -not $SecondAppProcess.HasExited) {
        & taskkill.exe /PID $SecondAppProcess.Id /T /F | Out-Null
    }
    if ($AppProcess -and -not $AppProcess.HasExited) {
        & taskkill.exe /PID $AppProcess.Id /T /F | Out-Null
    }
    if ($OrphanBackendProcess -and -not $OrphanBackendProcess.HasExited) {
        & taskkill.exe /PID $OrphanBackendProcess.Id /T /F | Out-Null
    }
    if ($Installed -and $Uninstaller -and (Test-Path $Uninstaller)) {
        $UninstallResult = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -Wait -PassThru
        if ($UninstallResult.ExitCode -ne 0) {
            throw "NSIS uninstaller exited with code $($UninstallResult.ExitCode)"
        }
        $UninstallDeadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $UninstallDeadline -and (Get-UninstallEntry)) {
            Start-Sleep -Milliseconds 250
        }
        if (Get-UninstallEntry) {
            throw "DeterminFlow uninstall registry entry still exists"
        }
        if (-not (Test-Path (Join-Path $UserData "config\models_config.json"))) {
            throw "Uninstaller removed persistent user configuration"
        }
        if ($Flavor -eq "full" -and -not (Test-Path (Join-Path $UserData "data\plugins\plugins.lock.json"))) {
            throw "Uninstaller removed persistent Plugin state"
        }
        Write-Output "NSIS uninstall and user-data preservation verified"
    }
}

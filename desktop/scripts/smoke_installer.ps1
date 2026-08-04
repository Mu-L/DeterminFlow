param(
    [Parameter(Mandatory = $true)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
$ProductName = "DeterminFlow"
$UserData = Join-Path $env:LOCALAPPDATA "io.determinflow.desktop"
$AppProcess = $null
$Uninstaller = $null
$Installed = $false

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
    Write-Output "Installed application status endpoint verified"
}
finally {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        & taskkill.exe /PID $AppProcess.Id /T /F | Out-Null
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
        Write-Output "NSIS uninstall and user-data preservation verified"
    }
}

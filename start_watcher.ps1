param(
  [string]$WatchDir = "",
  [string]$CompletedDir = "",
  [switch]$ScanExisting
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "venv\Scripts\python.exe"
if (!(Test-Path $python)) {
  Write-Error "Python venv not found at $python"
  exit 1
}

$settingsPath = Join-Path $root "watcher_settings.json"
$cfgWatch = "watch_incoming"
$cfgCompleted = ""
if (Test-Path $settingsPath) {
  try {
    $json = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -ne $json.watch_dir -and "$($json.watch_dir)" -ne "") {
      $cfgWatch = [string]$json.watch_dir
    }
    if ($null -ne $json.completed_video_dir -and "$($json.completed_video_dir)" -ne "") {
      $cfgCompleted = [string]$json.completed_video_dir
    } elseif ($null -ne $json.completed_dir -and "$($json.completed_dir)" -ne "") {
      $cfgCompleted = [string]$json.completed_dir
    }
  } catch {
    Write-Warning "Could not parse watcher_settings.json: $_"
  }
}

$effectiveWatch = if ($WatchDir -ne "") { $WatchDir } else { $cfgWatch }
$useExplicitCompleted = $PSBoundParameters.ContainsKey("CompletedDir") -and $CompletedDir -ne ""
$effectiveCompleted = if ($useExplicitCompleted) { $CompletedDir } else { $cfgCompleted }

$watchAbs = if ([System.IO.Path]::IsPathRooted($effectiveWatch)) { $effectiveWatch } else { Join-Path $root $effectiveWatch }
$statusFile = Join-Path $watchAbs "watch_status.json"
$logFile = Join-Path $watchAbs "watch_events.log"

if (Test-Path $statusFile) {
  try {
    $status = Get-Content $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $existingPid = [int]($status.pid | ForEach-Object { $_ })
    $existingRunning = [bool]$status.running
    if ($existingRunning -and $existingPid -gt 0) {
      $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
      if ($existingProcess) {
        Write-Output "Watcher already ON (PID $existingPid)"
        Write-Output "Watch folder: $watchAbs"
        Write-Output "Status file: $statusFile"
        Write-Output "Log file: $logFile"
        exit 0
      }
    }
  } catch {
    Write-Warning "Could not inspect existing watcher status: $_"
  }
}

$args = @("watch_and_transcribe.py", "--watch", $watchAbs, "--status-file", $statusFile, "--log-file", $logFile)
if ($effectiveCompleted -ne "") {
  $compAbs = if ([System.IO.Path]::IsPathRooted($effectiveCompleted)) { $effectiveCompleted } else { Join-Path $root $effectiveCompleted }
  $args += @("--completed", $compAbs)
}
if ($ScanExisting) { $args += "--scan-existing" }

Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden
Write-Output "Watcher ON"
Write-Output "Watch folder: $watchAbs"
Write-Output "Status file: $statusFile"
Write-Output "Log file: $logFile"

param(
  [string]$WatchDir = ""
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsPath = Join-Path $root "watcher_settings.json"
$cfgWatch = "watch_incoming"
if (Test-Path $settingsPath) {
  try {
    $json = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -ne $json.watch_dir -and "$($json.watch_dir)" -ne "") {
      $cfgWatch = [string]$json.watch_dir
    }
  } catch {
    Write-Warning "Could not parse watcher_settings.json: $_"
  }
}

$effectiveWatch = if ($WatchDir -ne "") { $WatchDir } else { $cfgWatch }
$watchAbs = if ([System.IO.Path]::IsPathRooted($effectiveWatch)) { $effectiveWatch } else { Join-Path $root $effectiveWatch }
$statusFile = Join-Path $watchAbs "watch_status.json"
$mediaExts = @('.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.mpeg', '.mpg', '.wmv', '.flv', '.mp3', '.m4a', '.wma', '.flac', '.ogg', '.wav')

if (!(Test-Path $statusFile)) {
  Write-Error "Status file not found: $statusFile"
  exit 1
}

$status = Get-Content $statusFile -Raw | ConvertFrom-Json
$wasRunning = [bool]$status.running
$watcherPid = [int]$status.pid

$process = Get-Process -Id $watcherPid -ErrorAction SilentlyContinue
if ($process) {
  try {
    # /T terminates the process tree (watcher + child transcription workers).
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    if (Test-Path $taskkill) {
      & $taskkill /PID $watcherPid /T /F | Out-Null
    }
    if (!(Test-Path $taskkill) -or $LASTEXITCODE -ne 0) {
      Stop-Process -Id $watcherPid -Force -ErrorAction Stop
    }
    Write-Output "Watcher OFF (stopped PID $watcherPid and child processes)"
  } catch {
    Write-Error "Failed to stop PID ${watcherPid}: $($_.Exception.Message)"
    exit 1
  }
} else {
  if ($wasRunning) {
    Write-Output "Watcher process not found (PID $watcherPid). Marking as OFF."
  } else {
    Write-Output "Watcher already OFF"
  }
}

$status.running = $false
$status.pid = $null
$status.status = "stopped"
$status.phase = "off"
$status.started_at = $null
$status.last_progress_at = $null
$status.last_progress_file = $null
$status.current_file = $null
$status.last_worker_pid = $null
$status.last_segment_end = $null
$status.active_jobs = @()
$status.last_event = "watcher_stopped_by_script"
$status.last_heartbeat = $null
$status.stalled = $false
$status.last_error = $null
$status.queue_count = @(
  Get-ChildItem -Path $watchAbs -File -ErrorAction SilentlyContinue |
    Where-Object { $mediaExts -contains $_.Extension.ToLowerInvariant() }
).Count
Get-ChildItem -Path $watchAbs -Filter '.watch_progress_*.json' -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
($status | ConvertTo-Json -Depth 5) | Set-Content -Path $statusFile -Encoding utf8

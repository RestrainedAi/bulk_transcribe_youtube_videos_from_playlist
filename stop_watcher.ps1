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
$taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
function Stop-Tree([int]$TreePid) {
  if ($TreePid -le 0) { return }
  if (!(Get-Process -Id $TreePid -ErrorAction SilentlyContinue)) { return }
  if (Test-Path $taskkill) { & $taskkill /PID $TreePid /T /F 2>&1 | Out-Null }
  Stop-Process -Id $TreePid -Force -ErrorAction SilentlyContinue
}
# DEV-174: our workers (Whisper worker, Granite/Voxtral eval child, ffmpeg) found by command line,
# so orphans whose watcher already died are stopped too.
function Get-OurWorkers {
  $rootLc = $root.ToLowerInvariant()
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $cl = "$($_.CommandLine)".ToLowerInvariant()
      ($cl -like "*watch_transcribe_worker.py*" -or $cl -like "*asr_eval_worker.py*" -or $cl -like "*watch_and_transcribe.py*") -and
      ($cl -like "*$rootLc*" -or $cl -like "*$($watchAbs.ToLowerInvariant())*")
    })
}
$workerPids = @()
foreach ($j in @($status.active_jobs)) { if ($j -and $j.worker_pid) { $workerPids += [int]$j.worker_pid } }
if ($status.last_worker_pid) { $workerPids += [int]$status.last_worker_pid }
Get-ChildItem -Path $watchAbs -Filter '.watch_progress_*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
  try { $pj = Get-Content $_.FullName -Raw | ConvertFrom-Json; if ($pj.worker_pid) { $workerPids += [int]$pj.worker_pid } } catch {}
}
try {
  if ($process) { Stop-Tree $watcherPid }
  foreach ($w in ($workerPids | Select-Object -Unique)) { Stop-Tree $w }
  foreach ($w in (Get-OurWorkers)) { Stop-Tree ([int]$w.ProcessId) }
  Start-Sleep -Milliseconds 500
  $left = @(Get-OurWorkers)
  if ($left.Count -gt 0) {
    Write-Output "Watcher NOT fully stopped: still running PID(s) $(($left | ForEach-Object { $_.ProcessId }) -join ', ')"
    exit 2
  }
} catch {
  Write-Error "Failed to stop PID ${watcherPid}: $($_.Exception.Message)"
  exit 1
}
if ($process) {
  Write-Output "Watcher OFF (stopped PID $watcherPid; verified no worker or Granite processes left)"
} elseif ($wasRunning) {
  Write-Output "Watcher process not found (PID $watcherPid). Marking as OFF; verified no workers left."
} else {
  Write-Output "Watcher already OFF (verified no workers left)"
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
# Keep progress files that say "completed": Reconcile and restart use them to mark the file done.
Get-ChildItem -Path $watchAbs -Filter '.watch_progress_*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
  $keep = $false
  try { $keep = ((Get-Content $_.FullName -Raw | ConvertFrom-Json).status -eq "completed") } catch {}
  if (-not $keep) { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
($status | ConvertTo-Json -Depth 5) | Set-Content -Path $statusFile -Encoding utf8

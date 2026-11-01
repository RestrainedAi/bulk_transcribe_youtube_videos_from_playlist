# Creates the "Transcription Workbench" Desktop shortcut with app icon (replaces the old
# "Transcription Watcher Control Panel" shortcut if present).
# Run from repo root: powershell -ExecutionPolicy Bypass -File .\Create_Watcher_Control_Panel_Shortcut.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetCmd = Join-Path $root "Open_Watcher_Control_Panel.cmd"
$targetVbs = Join-Path $root "Open_Watcher_Control_Panel.vbs"
$target = if (Test-Path $targetVbs) { $targetVbs } else { $targetCmd }
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Transcription Workbench.lnk"
$oldShortcut = Join-Path $desktop "Transcription Watcher Control Panel.lnk"
$iconIco = Join-Path $root "assets\watcher_desktop_icon.ico"
$iconPng = Join-Path $root "assets\watcher_desktop_icon.png"

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
$sc.WindowStyle = 1
$sc.Description = "Open Transcription Workbench"
if (Test-Path $iconIco) {
  $sc.IconLocation = "$iconIco,0"
} elseif (Test-Path $iconPng) {
  $sc.IconLocation = "$iconPng,0"
}
$sc.Save()
if (Test-Path $oldShortcut) { Remove-Item $oldShortcut -Force; Write-Output "Removed old shortcut: $oldShortcut" }

Write-Output "Shortcut created: $shortcutPath"
if (Test-Path $iconIco) {
  Write-Output "Icon: $iconIco"
} elseif (Test-Path $iconPng) {
  Write-Output "Icon: $iconPng (prefer .ico for shortcuts - run scripts\build_desktop_icon.py)"
} else {
  Write-Output "Note: add assets\watcher_desktop_icon.ico or .png"
}

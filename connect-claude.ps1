# Adds Limit Lamp's feeder to Claude Code's "statusLine" setting (or removes it with -Remove).
# Only ONE "statusLine" entry is added to ~/.claude/settings.json, after a timestamped backup.
# If you already have another statusLine, it is left untouched and you get a warning.
param([switch]$Remove, [string]$SettingsPath = (Join-Path $env:USERPROFILE '.claude\settings.json'))
$ErrorActionPreference = 'Stop'

function Find-Python {
  foreach ($c in @((Get-Command python -ErrorAction SilentlyContinue).Source, (Get-Command py -ErrorAction SilentlyContinue).Source)) {
    if (-not $c -or $c -like '*\WindowsApps\*') { continue }
    $exe = if ($c -like '*\py.exe') { (& $c -3 -c 'import sys; print(sys.executable)') } else { $c }
    if ($exe -and (Test-Path $exe)) { return $exe }
  }
  return $null
}

$feedPath = Join-Path $env:LOCALAPPDATA 'LimitLamp\app\limitlamp_feed.py'
if (-not $Remove) {
  if (-not (Test-Path $feedPath)) { throw "Limit Lamp is not installed ($feedPath). Run .\install.ps1 first." }
  $py = Find-Python
  if (-not $py) { throw "Python 3 was not found." }
  $cmd = '"' + $py.Replace([string][char]92, '/') + '" -I -S "' + $feedPath.Replace([string][char]92, '/') + '"'
}

if (-not (Test-Path $SettingsPath)) {
  if ($Remove) { Write-Host "No settings.json, nothing to do."; return }
  New-Item -ItemType Directory -Force (Split-Path $SettingsPath) | Out-Null
  [IO.File]::WriteAllText($SettingsPath, "{}`n", (New-Object Text.UTF8Encoding($false)))
}
$text = [IO.File]::ReadAllText($SettingsPath)
$null = $text | ConvertFrom-Json          # stops here (file untouched) if it is not valid JSON

$bak = "$SettingsPath.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item $SettingsPath $bak

# our entry (current python feeder, or the older "limitlamp-feed.exe" / "usage-feed" builds)
$mine = '"statusLine"\s*:\s*\{[^{}]*(limitlamp[-_]feed|usage-feed)[^{}]*\}\s*,?'
if ($Remove) {
  if ($text -notmatch $mine) { Write-Host "Our statusLine entry was not found; nothing changed."; Remove-Item $bak; return }
  $new = [regex]::Replace($text, '[ \t]*' + $mine + '[ \t]*\r?\n?', '')
} else {
  if ($text -match $mine) {
    # replace an older entry of ours with the current command
    $text = [regex]::Replace($text, '[ \t]*' + $mine + '[ \t]*\r?\n?', '')
  }
  if ($text -match '"statusLine"\s*:') {
    Remove-Item $bak
    Write-Host "WARNING: settings.json already has another statusLine - not changed."
    Write-Host "To chain it manually, pipe the same stdin into:  $cmd"
    return
  }
  $entry = '"statusLine": { "type": "command", "command": "' + $cmd.Replace('"', '\"') + '" },'
  $idx = $text.IndexOf('{')
  $rest = $text.Substring($idx + 1)
  $sep = if ($rest.Trim().StartsWith('}')) { $entry.TrimEnd(',') } else { $entry }
  $new = $text.Substring(0, $idx + 1) + "`n  " + $sep + $rest
}

try { $null = $new | ConvertFrom-Json } catch { Remove-Item $bak -ErrorAction SilentlyContinue; throw "Result was not valid JSON; settings.json left untouched: $_" }
[IO.File]::WriteAllText($SettingsPath, $new, (New-Object Text.UTF8Encoding($false)))
if ($Remove) { Write-Host "statusLine entry removed. Backup: $bak" }
else { Write-Host "Linked. Backup: $bak"; Write-Host "It takes effect in new Claude Code sessions." }

# Limit Lamp - install (no admin needed; writes only to your user folders)
#   .\install.ps1               install / update
#   .\install.ps1 -Uninstall    remove (your data folder is kept)
# Requires Python 3.9+ with tkinter and Pillow:  python -m pip install pillow
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Join-Path $env:LOCALAPPDATA 'LimitLamp'
$app  = Join-Path $root 'app'
$lnk  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Limit Lamp.lnk'

function Stop-LimitLamp {
  Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*limitlamp.pyw*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Milliseconds 400
}

if ($Uninstall) {
  Stop-LimitLamp
  Remove-Item $app -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $root 'LimitLamp.ico') -Force -ErrorAction SilentlyContinue
  Remove-Item $lnk -Force -ErrorAction SilentlyContinue
  Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name LimitLamp -ErrorAction SilentlyContinue
  Write-Host "Removed. Your data (settings, history, sign-in token) is still in $root - delete the folder if you want it gone."
  Write-Host "To remove the Claude Code link too:  .\connect-claude.ps1 -Remove"
  return
}

# --- find a real Python (not the Microsoft Store stub) ---
function Find-Python {
  foreach ($c in @((Get-Command python -ErrorAction SilentlyContinue).Source, (Get-Command py -ErrorAction SilentlyContinue).Source)) {
    if (-not $c -or $c -like '*\WindowsApps\*') { continue }
    $exe = if ($c -like '*\py.exe') { (& $c -3 -c 'import sys; print(sys.executable)') } else { $c }
    if ($exe -and (Test-Path $exe)) { return $exe }
  }
  return $null
}
$py = Find-Python
if (-not $py) { throw "Python 3 was not found. Install it from python.org (tick 'Add to PATH'), then run this again." }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { throw "pythonw.exe is missing next to $py" }
& $py -c 'import sys, tkinter, PIL; assert sys.version_info >= (3, 9)' 2>$null
if ($LASTEXITCODE) { throw "Python needs tkinter and Pillow. Run:  `"$py`" -m pip install pillow" }

Stop-LimitLamp
New-Item -ItemType Directory -Force $app | Out-Null
Remove-Item (Join-Path $app 'limitlamp') -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $here 'app\*') $app -Recurse -Force
Get-ChildItem $app -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$ico = Join-Path $root 'LimitLamp.ico'
& $py (Join-Path $app 'limitlamp.pyw') --make-icon $ico

New-Item -ItemType Directory -Force (Split-Path $lnk) | Out-Null
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $app 'limitlamp.pyw') + '"'
$s.WorkingDirectory = $app
$s.IconLocation = $ico
$s.Description = 'Claude usage limits panel'
$s.Save()

Write-Host "Installed: $app"
Write-Host "Start it from the Start Menu ('Limit Lamp') or run:  & `"$pyw`" `"$app\limitlamp.pyw`""
Write-Host "Next (optional, no sign-in needed):  .\connect-claude.ps1   - links Claude Code's status line to the panel"

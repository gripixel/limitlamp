# Kotempo - kurulum (yonetici gerekmez, sadece kullanici klasorune yazar)
#   .\install.ps1            kur / guncelle
#   .\install.ps1 -Uninstall kaldir (veri klasorune dokunmaz)
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$root    = Join-Path $env:LOCALAPPDATA 'Kotempo'
$bin     = Join-Path $root 'bin'
$lnk     = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Kotempo.lnk'

if ($Uninstall) {
  Get-Process Kotempo -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Milliseconds 500
  Remove-Item $bin -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $lnk -Force -ErrorAction SilentlyContinue
  Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name Kotempo -ErrorAction SilentlyContinue
  Write-Host "Kaldirildi. (Veri: $root\limits.txt, history.csv korundu; istersen klasoru sil.)"
  Write-Host "Claude Code baglantisini da kaldirmak icin: .\connect-claude.ps1 -Remove"
  return
}

foreach ($f in 'Kotempo.exe','kotempo-feed.exe') {
  if (-not (Test-Path (Join-Path $here "bin\$f"))) { throw "bin\$f yok. Once .\build.ps1 calistir." }
}
Get-Process Kotempo -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $here 'bin\*.exe') $bin -Force

$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
$s.TargetPath = Join-Path $bin 'Kotempo.exe'
$s.WorkingDirectory = $bin
$s.Description = 'Claude kullanim limitleri paneli'
$s.Save()

Write-Host "Kuruldu: $bin"
Write-Host "Baslat menusunden 'Kotempo' ile ac."
Write-Host "Siradaki adim: .\connect-claude.ps1  (Claude Code'u panele baglar)"

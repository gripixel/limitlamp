# Claude Kullanim Paneli - kurulum (yonetici gerekmez, sadece kullanici klasorune yazar)
#   .\install.ps1            kur / guncelle
#   .\install.ps1 -Uninstall kaldir (veri klasorune dokunmaz)
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$root    = Join-Path $env:LOCALAPPDATA 'ClaudeUsagePanel'
$bin     = Join-Path $root 'bin'
$lnk     = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Claude Kullanım Paneli.lnk'

if ($Uninstall) {
  Get-Process ClaudeUsagePanel -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Milliseconds 500
  Remove-Item $bin -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $lnk -Force -ErrorAction SilentlyContinue
  Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name ClaudeUsagePanel -ErrorAction SilentlyContinue
  Write-Host "Kaldirildi. (Veri: $root\limits.txt, history.csv korundu; istersen klasoru sil.)"
  Write-Host "Claude Code baglantisini da kaldirmak icin: .\connect-claude.ps1 -Remove"
  return
}

foreach ($f in 'ClaudeUsagePanel.exe','usage-feed.exe') {
  if (-not (Test-Path (Join-Path $here "bin\$f"))) { throw "bin\$f yok. Once .\build.ps1 calistir." }
}
Get-Process ClaudeUsagePanel -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $here 'bin\*.exe') $bin -Force

$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
$s.TargetPath = Join-Path $bin 'ClaudeUsagePanel.exe'
$s.WorkingDirectory = $bin
$s.Description = 'Claude kullanim limitleri paneli'
$s.Save()

Write-Host "Kuruldu: $bin"
Write-Host "Baslat menusunden 'Claude Kullanım Paneli' ile ac."
Write-Host "Siradaki adim: .\connect-claude.ps1  (Claude Code'u panele baglar)"

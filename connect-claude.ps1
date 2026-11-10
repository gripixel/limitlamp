# Claude Code'un statusLine ayarina kotempo-feed.exe'yi ekler (ya da -Remove ile kaldirir).
# ~/.claude/settings.json'a sadece BIR satirlik "statusLine" girisi eklenir; once yedek alinir.
# Baska bir statusLine zaten varsa dokunmaz (kendi komutunu bozmamak icin) ve seni uyarir.
param([switch]$Remove, [string]$SettingsPath = (Join-Path $env:USERPROFILE '.claude\settings.json'))
$ErrorActionPreference = 'Stop'
$feed = (Join-Path $env:LOCALAPPDATA 'Kotempo\bin\kotempo-feed.exe').Replace([string][char]92, '/')
if (-not $Remove -and -not (Test-Path $feed)) { throw "kotempo-feed.exe kurulu degil ($feed). Once .\install.ps1 calistir." }

if (-not (Test-Path $SettingsPath)) {
  if ($Remove) { Write-Host "settings.json yok, yapilacak bir sey yok."; return }
  New-Item -ItemType Directory -Force (Split-Path $SettingsPath) | Out-Null
  [IO.File]::WriteAllText($SettingsPath, "{}`n", (New-Object Text.UTF8Encoding($false)))
}
$text = [IO.File]::ReadAllText($SettingsPath)
$null = $text | ConvertFrom-Json   # gecerli JSON degilse burada durur, dosyaya dokunmaz

$bak = "$SettingsPath.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item $SettingsPath $bak

$mine = '"statusLine"\s*:\s*\{[^{}]*kotempo-feed[^{}]*\}\s*,?'
if ($Remove) {
  if ($text -notmatch $mine) { Write-Host "Bizim statusLine girisimiz bulunamadi; dokunulmadi."; Remove-Item $bak; return }
  $new = [regex]::Replace($text, '[ \t]*' + $mine + '[ \t]*\r?\n?', '')
} else {
  if ($text -match $mine) { Write-Host "Zaten bagli."; Remove-Item $bak; return }
  if ($text -match '"statusLine"\s*:') {
    Remove-Item $bak
    Write-Host "UYARI: settings.json'da baska bir statusLine var, degistirmedim."
    Write-Host "Mevcut durum cubugu komutunu bozmamak icin otomatik eklemiyorum. Elle zincirlemek icin: kotempo-feed.exe'ye ayni stdin'i ver ($feed)."
    return
  }
  $entry = '"statusLine": { "type": "command", "command": "\"' + $feed + '\"" },'
  $idx = $text.IndexOf('{')
  $rest = $text.Substring($idx + 1)
  $sep = if ($rest.Trim().StartsWith('}')) { ($entry.TrimEnd(',')) } else { $entry }
  $new = $text.Substring(0, $idx + 1) + "`n  " + $sep + $rest
}

try { $null = $new | ConvertFrom-Json } catch { Remove-Item $bak -ErrorAction SilentlyContinue; throw "Sonuc gecerli JSON olmadi, dosyaya dokunulmadi: $_" }
[IO.File]::WriteAllText($SettingsPath, $new, (New-Object Text.UTF8Encoding($false)))
if ($Remove) { Write-Host "statusLine girisi kaldirildi. Yedek: $bak" }
else { Write-Host "Baglandi. Yedek: $bak"; Write-Host "Claude Code'u (Desktop'ta yeni oturum) yeniden baslatinca etkinlesir." }

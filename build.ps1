# Derleme: Windows'un icindeki .NET Framework 4.8 derleyicisi (csc.exe). Hicbir sey indirilmez.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$csc  = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc.exe bulunamadi: $csc" }
New-Item -ItemType Directory -Force (Join-Path $here 'bin') | Out-Null
Push-Location $here
try {
  & $csc /nologo /target:winexe /optimize+ /warn:4 /out:bin\Kotempo.exe /win32manifest:src\app.manifest `
         /r:System.Windows.Forms.dll /r:System.Drawing.dll src\Panel.cs src\Shared.cs
  if ($LASTEXITCODE) { throw "Panel derlenemedi" }
  & $csc /nologo /target:exe /optimize+ /warn:4 /out:bin\kotempo-feed.exe `
         /r:System.Web.Extensions.dll src\Feed.cs src\Shared.cs
  if ($LASTEXITCODE) { throw "Besleyici derlenemedi" }
  Write-Host "Tamam: bin\Kotempo.exe, bin\kotempo-feed.exe"
} finally { Pop-Location }

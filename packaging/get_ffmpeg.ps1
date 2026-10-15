# Downloads a static FFmpeg build (ffmpeg.exe + ffprobe.exe) into packaging\ffmpeg for the exe.
# Source: gyan.dev "release essentials" build. Run once; build_exe.bat skips it when present.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest very slow

$target = Join-Path $PSScriptRoot 'ffmpeg'
$url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$zip = Join-Path $env:TEMP 'subforge-ffmpeg.zip'
$unpacked = Join-Path $env:TEMP 'subforge-ffmpeg'

Write-Host "Downloading FFmpeg from $url ..."
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $unpacked

New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($name in 'ffmpeg.exe', 'ffprobe.exe') {
    $file = Get-ChildItem -Path $unpacked -Recurse -Filter $name | Select-Object -First 1
    if (-not $file) { throw "$name not found in the FFmpeg archive" }
    Copy-Item $file.FullName -Destination $target -Force
}
$license = Get-ChildItem -Path $unpacked -Recurse -Filter 'LICENSE*' | Select-Object -First 1
if ($license) { Copy-Item $license.FullName -Destination (Join-Path $target 'LICENSE.txt') -Force }

Remove-Item $zip -Force
Remove-Item $unpacked -Recurse -Force
Write-Host "FFmpeg ready in $target"

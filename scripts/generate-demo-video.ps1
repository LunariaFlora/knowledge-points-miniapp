$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$mediaDir = Join-Path $projectRoot 'web\media'
$outputFile = Join-Path $mediaDir 'knowledge-demo.mp4'

if (Test-Path -LiteralPath $outputFile) {
    Write-Output "Demo video already exists: $outputFile"
    exit 0
}

New-Item -ItemType Directory -Force -Path $mediaDir | Out-Null
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source

& $ffmpeg -hide_banner -loglevel error -y `
  -f lavfi -i "color=c=0x174a44:s=960x540:r=25:d=45" `
  -f lavfi -i "sine=frequency=260:sample_rate=44100:duration=45" `
  -vf "drawbox=x='mod(t*70,1100)-140':y=0:w=140:h=540:color=0x2f8478@0.32:t=fill,drawbox=x='960-mod(t*45,1120)':y=0:w=90:h=540:color=0x8cd6c7@0.16:t=fill" `
  -af "volume=0" `
  -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p `
  -c:a aac -b:a 32k -movflags +faststart -shortest `
  $outputFile

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Generated demo video: $outputFile"

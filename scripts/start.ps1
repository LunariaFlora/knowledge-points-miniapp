$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

powershell -ExecutionPolicy Bypass -File scripts\generate-demo-video.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:ENABLE_DEV_ENDPOINTS = '1'
python app\server.py --host 127.0.0.1 --port 8765

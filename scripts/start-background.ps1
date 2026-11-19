$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logsDirectory = Join-Path $projectRoot 'logs'
$outputLog = Join-Path $logsDirectory 'server.out.log'
$errorLog = Join-Path $logsDirectory 'server.err.log'
$pythonExecutable = (Get-Command python -ErrorAction Stop).Source

New-Item -ItemType Directory -Force -Path $logsDirectory | Out-Null

$process = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList @('app\server.py', '--host', '127.0.0.1', '--port', '8765', '--reset') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outputLog `
    -RedirectStandardError $errorLog `
    -PassThru

Write-Output "STARTED_PID=$($process.Id)"

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

python scripts\validate_design.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python tests\database_backup_restore_test.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

node tests\miniprogram-api-cache.test.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

node tests\miniprogram-pages.test.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

node tests\miniprogram-complete-pages.test.js
exit $LASTEXITCODE

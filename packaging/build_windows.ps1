$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Сборка EXE требует Windows x64.' }
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name Taverna --paths app/backend packaging/launch.py
if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки Taverna.exe' }
python -m PyInstaller --noconfirm --clean --onedir --console --name Taverna-Diagnostics --paths app/backend packaging/diagnostics.py
if ($LASTEXITCODE -ne 0) { throw 'Ошибка диагностической сборки' }
Copy-Item dist/Taverna-Diagnostics/* dist/Taverna/ -Recurse -Force
New-Item dist/Taverna/app/web -ItemType Directory -Force | Out-Null
Copy-Item app/web/dist dist/Taverna/app/web/ -Recurse -Force
Copy-Item README.md dist/Taverna/
New-Item dist/Taverna/docs -ItemType Directory -Force | Out-Null
Copy-Item docs/ARCHITECTURE.md dist/Taverna/docs/
python scripts/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'Неполный комплект лицензий' }
Copy-Item dist/licenses dist/Taverna/LICENSES -Recurse -Force
Compress-Archive -Path dist/Taverna/* -DestinationPath dist/Taverna-Internal-Windows-x64.zip -Force
Get-FileHash dist/Taverna-Internal-Windows-x64.zip -Algorithm SHA256 | Format-List

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m pip install -r agent\build-requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install connector build dependencies.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --onefile --windowed --name PrintSetu-Connector --paths . agent\desktop_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Connector packaging failed.' }
Write-Host 'Ready: dist\PrintSetu-Connector.exe'
$compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$localCompiler = Join-Path $PSScriptRoot '.tools\Inno Setup\ISCC.exe'
if ($compiler) { $compilerPath = $compiler.Source }
elseif (Test-Path -LiteralPath $localCompiler) { $compilerPath = $localCompiler }
else { throw 'Install Inno Setup 6 or 7, then place ISCC.exe on PATH or install it under .tools\Inno Setup. The portable executable was built successfully.' }
& $compilerPath agent\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
Write-Host 'Ready: dist\PrintSetu-Setup.exe'

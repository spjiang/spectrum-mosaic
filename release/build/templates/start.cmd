@echo off
cd /d "%~dp0"
rem Windows PowerShell 5.1 按系统编码读无 BOM 的 UTF-8，中文会把引号冲掉。
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $PWD 'start.ps1'; $b=[IO.File]::ReadAllBytes($p); if(-not($b.Length -ge 3 -and $b[0]-eq 239 -and $b[1]-eq 187 -and $b[2]-eq 191)){ $utf8=New-Object System.Text.UTF8Encoding $false; $bom=New-Object System.Text.UTF8Encoding $true; [IO.File]::WriteAllText($p,$utf8.GetString($b),$bom) }"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
pause

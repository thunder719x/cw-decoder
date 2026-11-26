@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "F:\QQBot\AstrBot\.venv\Scripts\python.exe" set "PY=F:\QQBot\AstrBot\.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY set "PY=python"

echo ============================================
echo   CW Decoder - 上传/更新到 GitHub
echo ============================================
echo.

"%PY%" "%~dp0tools\upload_github.py" %*

echo.
echo 按任意键关闭...
pause >nul

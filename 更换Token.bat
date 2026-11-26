@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "F:\QQBot\AstrBot\.venv\Scripts\python.exe" set "PY=F:\QQBot\AstrBot\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo ============================================
echo   更换 GitHub Token
echo ============================================
echo.
echo 请先在浏览器里：把新的 Token 复制到剪贴板
echo 然后回到这个窗口，按任意键继续...
pause >nul
echo.

"%PY%" "%~dp0tools\upload_github.py" --reset-token --yes %*

echo.
echo 按任意键关闭...
pause >nul

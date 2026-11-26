@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先使用装有 numpy/onnxruntime 的解释器
set "PY="
if exist "F:\QQBot\AstrBot\.venv\Scripts\python.exe" set "PY=F:\QQBot\AstrBot\.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY set "PY=python"

"%PY%" -c "import numpy, sounddevice" 2>nul
if errorlevel 1 (
    echo.
    echo [错误] 当前 Python 缺少依赖：
    echo         %PY%
    echo         请确认 numpy / sounddevice 已安装，或改用：
    echo         F:\QQBot\AstrBot\.venv\Scripts\python.exe run.py
    echo.
    pause
    exit /b 1
)

"%PY%" run.py
if errorlevel 1 (
    echo.
    echo [程序异常退出] 错误信息见上
    pause
)

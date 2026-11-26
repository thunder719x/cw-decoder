@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 找一个「装了 numpy / scipy / sounddevice」的 Python：
rem   1) python_path.txt 里写死的路径（本机使用，不随仓库发布）
rem   2) 程序目录下的 python\python.exe（便携版）
rem   3) py -3 / python / python3
set "PY="

if exist "%~dp0python_path.txt" (
    set /p PY=<"%~dp0python_path.txt"
)

if not defined PY if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY call :try python3

if not defined PY (
    echo.
    echo [错误] 没找到装有依赖的 Python。
    echo.
    echo   1. 安装依赖：pip install -r requirements.txt
    echo   2. 或者在本目录建虚拟环境：python -m venv .venv
    echo      然后：.venv\Scripts\pip install -r requirements.txt
    echo   3. 如果机器上有多个 Python，可在本目录新建 python_path.txt，
    echo      第一行写解释器的完整路径，例如：
    echo      C:\Python312\python.exe
    echo.
    pause
    exit /b 1
)

%PY% run.py %*
exit /b %errorlevel%

:try
if defined PY exit /b 0
%* -c "import numpy, scipy, sounddevice" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%*"
exit /b 0

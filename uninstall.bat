@echo off
REM ──────────────────────────────────────────────────────────────
REM  Datonis Edge Server — Uninstall Service
REM  Usage:  Run as Administrator
REM ──────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

set SERVICE_NAME=DatonisEdgeServer
set SCRIPT_DIR=%~dp0
set EDGE_DIR=%SCRIPT_DIR%
set VENV_DIR=%EDGE_DIR%venv

echo.
echo ════════════════════════════════════════════════════════
echo    Datonis Edge Server — Uninstaller (Windows)
echo ════════════════════════════════════════════════════════
echo.

REM ── Check admin privileges ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be run as Administrator.
    pause
    exit /b 1
)

REM ── Step 1: Kill running process ──
echo [INFO]  Stopping Edge Server process...
taskkill /FI "WINDOWTITLE eq Datonis Edge Server" /F >nul 2>&1
REM Also kill any python running main.py
wmic process where "commandline like '%%main.py%%' and name='python.exe'" call terminate >nul 2>&1
echo [OK]    Process stopped

REM ── Step 2: Remove scheduled task ──
schtasks /query /tn "%SERVICE_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    schtasks /delete /tn "%SERVICE_NAME%" /f >nul 2>&1
    echo [OK]    Scheduled task removed
) else (
    echo [INFO]  No scheduled task found
)

REM ── Step 3: Remove virtual environment ──
if exist "%VENV_DIR%" (
    rmdir /s /q "%VENV_DIR%"
    echo [OK]    Virtual environment removed
) else (
    echo [INFO]  No virtual environment found
)

REM ── Step 4: Remove wrapper script ──
if exist "%EDGE_DIR%run_service.bat" (
    del /F /Q "%EDGE_DIR%run_service.bat"
    echo [OK]    Wrapper script removed
)

echo.
echo ════════════════════════════════════════════════════════
echo    Uninstall complete!
echo.
echo    The service has been stopped and removed.
echo    Your config, data, logs, and backups are preserved.
echo ════════════════════════════════════════════════════════
echo.
pause

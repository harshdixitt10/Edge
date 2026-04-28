@echo off
REM ──────────────────────────────────────────────────────────────
REM  Datonis Edge Server — Install & Run as Windows Service
REM  Usage:  Run as Administrator — right-click > Run as administrator
REM ──────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

set SERVICE_NAME=DatonisEdgeServer
set DISPLAY_NAME=Datonis Edge Server
set SCRIPT_DIR=%~dp0
set EDGE_DIR=%SCRIPT_DIR%
set VENV_DIR=%EDGE_DIR%venv
set PARENT_DIR=%EDGE_DIR%..

echo.
echo ════════════════════════════════════════════════════════
echo    Datonis Edge Server — Installer (Windows)
echo ════════════════════════════════════════════════════════
echo.

REM ── Check admin privileges ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be run as Administrator.
    echo         Right-click and select "Run as administrator".
    pause
    exit /b 1
)

REM ── Find Python ──
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.8+ from https://www.python.org
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [INFO]  Python found: %PYVER%
echo [INFO]  Edge server directory: %EDGE_DIR%
echo.

REM ── Step 1: Stop existing service if running ──
sc query %SERVICE_NAME% >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARN]  Existing service found — stopping and removing...
    net stop %SERVICE_NAME% >nul 2>&1
    sc delete %SERVICE_NAME% >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo [OK]    Old service removed
)

REM ── Step 2: Fresh configuration ──
echo [INFO]  Ensuring fresh configuration...
if exist "%EDGE_DIR%config.template.yaml" (
    copy /Y "%EDGE_DIR%config.template.yaml" "%EDGE_DIR%config.yaml" >nul
    echo [OK]    config.yaml reset to template (fresh)
) else (
    echo [WARN]  config.template.yaml not found — keeping existing config.yaml
)

REM Remove old database
if exist "%PARENT_DIR%\data\edge_server.db" (
    del /F /Q "%PARENT_DIR%\data\edge_server.db"
    echo [OK]    Old database removed (fresh start)
)

REM ── Step 3: Create virtual environment ──
echo [INFO]  Setting up Python virtual environment...
python -m venv "%VENV_DIR%"
echo [OK]    Virtual environment created

REM ── Step 4: Install dependencies ──
echo [INFO]  Bootstrapping pip inside virtual environment...
"%VENV_DIR%\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1

echo [INFO]  Upgrading pip / setuptools / wheel to latest...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [WARN]  pip upgrade failed — retrying with bootstrap from get-pip.py
    powershell -Command "Invoke-WebRequest -Uri https://bootstrap.pypa.io/get-pip.py -OutFile '%TEMP%\get-pip.py'" >nul 2>&1
    if exist "%TEMP%\get-pip.py" (
        "%VENV_DIR%\Scripts\python.exe" "%TEMP%\get-pip.py" --force-reinstall
        del /F /Q "%TEMP%\get-pip.py" >nul 2>&1
    ) else (
        echo [ERROR] Could not download get-pip.py. Check internet connection.
        pause
        exit /b 1
    )
)
echo [OK]    pip is up to date

echo [INFO]  Installing project dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%EDGE_DIR%requirements.txt" --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies. See messages above.
    pause
    exit /b 1
)
echo [OK]    Dependencies installed

REM ── Step 4b: Bootstrap default credentials (deterministic, install-time) ──
REM This computes a real bcrypt hash for "changeme" on this machine and writes
REM it into config.yaml, plus the local credential backup file. We use --force
REM here because install.bat already reset config.yaml from the template, and
REM any pre-existing hash should be discarded for a fresh install.
echo [INFO]  Bootstrapping default credentials (admin / changeme)...
"%VENV_DIR%\Scripts\python.exe" "%EDGE_DIR%scripts\bootstrap_credentials.py" --force
if %errorlevel% neq 0 (
    echo [ERROR] Credential bootstrap failed. Login will not work until this is fixed.
    pause
    exit /b 1
)
echo [OK]    Default credentials configured

REM ── Step 5: Create required directories ──
if not exist "%PARENT_DIR%\logs" mkdir "%PARENT_DIR%\logs"
if not exist "%PARENT_DIR%\data" mkdir "%PARENT_DIR%\data"
if not exist "%PARENT_DIR%\Snapshot Backup" mkdir "%PARENT_DIR%\Snapshot Backup"
if not exist "%PARENT_DIR%\Configuration Backup\opcua_conf_backup" mkdir "%PARENT_DIR%\Configuration Backup\opcua_conf_backup"
if not exist "%PARENT_DIR%\Configuration Backup\csv_conf_backup" mkdir "%PARENT_DIR%\Configuration Backup\csv_conf_backup"
if not exist "%PARENT_DIR%\Configuration Backup\mtconnect_conf_backup" mkdir "%PARENT_DIR%\Configuration Backup\mtconnect_conf_backup"
echo [OK]    Directories created

REM ── Step 6: Create a wrapper script for the service ──
(
echo @echo off
echo cd /d "%EDGE_DIR%"
echo "%VENV_DIR%\Scripts\python.exe" main.py
) > "%EDGE_DIR%run_service.bat"

REM ── Step 7: Install as Windows Service using sc + NSSM or direct method ──
REM We use a scheduled task as a lightweight service alternative since
REM sc.exe requires a proper Windows Service executable.
echo [INFO]  Creating Windows scheduled task (runs at startup)...

schtasks /create /tn "%SERVICE_NAME%" /tr "\"%EDGE_DIR%run_service.bat\"" /sc onstart /ru SYSTEM /rl HIGHEST /f >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK]    Scheduled task created: %SERVICE_NAME%
) else (
    echo [WARN]  Could not create scheduled task. You may need to start manually.
)

REM ── Step 8: Start now ──
echo [INFO]  Starting Edge Server...
start "Datonis Edge Server" /min "%EDGE_DIR%run_service.bat"
echo [OK]    Server started in background

echo.
echo ════════════════════════════════════════════════════════
echo    Installation complete!
echo.
echo    Service name:  %SERVICE_NAME%
echo    Web UI:        http://localhost:8082
echo    Login:         admin / changeme
echo.
echo    To stop:   taskkill /FI "WINDOWTITLE eq Datonis Edge Server"
echo    To remove: Run uninstall.bat as Administrator
echo ════════════════════════════════════════════════════════
echo.
pause

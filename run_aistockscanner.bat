@echo off
REM ============================================================
REM AIStockScanner Windows One-Click Launcher
REM ============================================================
REM This script starts AIStockScanner with PostgreSQL checks.
REM Double-click to run. Terminal stays open for error visibility.
REM ============================================================

cd /d "%~dp0"

title AIStockScanner

echo ============================================================
echo                  AIStockScanner
echo ============================================================
echo Project: AIStockScanner
echo Database: PostgreSQL / ai_stock_scanner
echo URL: http://127.0.0.1:8004
echo ============================================================
echo.

REM ------------------------------------------------------------------
REM 1. Verify uv is available
REM ------------------------------------------------------------------
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo ======================================================
    echo AIStockScanner cannot start
    echo uv was not found in PATH.
    echo Please install uv: https://docs.astral.sh/uv/getting-started/installation/
    echo ======================================================
    pause
    exit /b 1
)
echo [OK] uv found.

REM ------------------------------------------------------------------
REM 2. PostgreSQL configuration
REM ------------------------------------------------------------------
set "PG_VERSION=18"
set "PG_BIN=C:\Program Files\PostgreSQL\%PG_VERSION%\bin"
set "PG_DATA=C:\Program Files\PostgreSQL\%PG_VERSION%\data"
set "PG_SERVICE=postgresql-x64-18"
set "PG_PORT=5432"
set "PG_DB=ai_stock_scanner"

REM ------------------------------------------------------------------
REM 3. Check PostgreSQL readiness
REM ------------------------------------------------------------------
echo Checking PostgreSQL...
"%PG_BIN%\pg_isready.exe" -h localhost -p %PG_PORT% -d %PG_DB% -q
if %errorlevel% equ 0 (
    echo [OK] PostgreSQL is ready on port %PG_PORT%.
    goto :START_APP
)

echo [WARN] PostgreSQL not accepting connections.
echo Attempting standard service start: net start %PG_SERVICE%
net start %PG_SERVICE% >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Service start requested. Waiting for readiness...
    timeout /t 5 /nobreak >nul
    "%PG_BIN%\pg_isready.exe" -h localhost -p %PG_PORT% -d %PG_DB% -q
    if %errorlevel% equ 0 (
        echo [OK] PostgreSQL is ready on port %PG_PORT%.
        goto :START_APP
    )
)

echo [INFO] Service start did not yield ready state. Trying pg_ctl...
"%PG_BIN%\pg_ctl.exe" -D %PG_DATA% start -w -t 30 >nul 2>nul
timeout /t 3 /nobreak >nul
"%PG_BIN%\pg_isready.exe" -h localhost -p %PG_PORT% -d %PG_DB% -q
if %errorlevel% equ 0 (
    echo [OK] PostgreSQL is ready on port %PG_PORT%.
    goto :START_APP
)

REM ------------------------------------------------------------------
REM 4. PostgreSQL startup failed
REM ------------------------------------------------------------------
echo.
echo ======================================================
echo DATABASE ERROR
echo PostgreSQL is not accepting connections on port %PG_PORT%.
echo.
echo AIStockScanner was NOT started.
echo.
echo Check PostgreSQL logs/service:
echo   Service: %PG_SERVICE%
echo   Data:    %PG_DATA%
echo   Logs:    C:\Program Files\PostgreSQL\%PG_VERSION%\data\log\
echo ======================================================
pause
exit /b 1

:START_APP
echo.
echo Starting AIStockScanner...
echo.
echo Browser will open automatically at http://127.0.0.1:8004
echo Press CTRL+C to stop the application.
echo ============================================================

REM ------------------------------------------------------------------
REM 5. Launch server and auto-open browser after short delay
REM ------------------------------------------------------------------
start "" cmd /c "timeout /t 4 /nobreak >nul && start "" http://127.0.0.1:8004"

uv run uvicorn app.main:app --host 127.0.0.1 --port 8004

REM ------------------------------------------------------------------
REM 6. If we reach here, server stopped (Ctrl+C or error)
REM ------------------------------------------------------------------
echo.
echo ==============================================================
echo AIStockScanner stopped.
echo ==============================================================
pause
@echo off
:: AutoPackager - Start Redis Server
:: Redis is required as the Celery message broker.

setlocal

echo.
echo  Starting Redis server...
echo  Press Ctrl+C to stop.
echo.

:: Try common Redis install locations
where redis-server >nul 2>&1
if %ERRORLEVEL% equ 0 (
    redis-server
    goto :eof
)

if exist "C:\Program Files\Redis\redis-server.exe" (
    "C:\Program Files\Redis\redis-server.exe"
    goto :eof
)

if exist "%LOCALAPPDATA%\Redis\redis-server.exe" (
    "%LOCALAPPDATA%\Redis\redis-server.exe"
    goto :eof
)

:: Try WSL as a fallback
where wsl >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo  Redis not found natively, starting via WSL...
    wsl redis-server
    goto :eof
)

echo.
echo  [ERROR] redis-server not found.
echo  Install Redis for Windows or ensure it is on your PATH.
echo  Download: https://github.com/tporadowski/redis/releases
echo.
pause
endlocal

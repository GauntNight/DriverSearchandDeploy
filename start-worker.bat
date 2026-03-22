@echo off
:: AutoPackager - Start Celery Worker
:: Starts the background worker that processes packaging jobs.
:: Redis must be running first (see start-redis.bat).

setlocal

cd /d "%~dp0"

:: Activate virtualenv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo.
echo  Starting AutoPackager Celery worker...
echo  Press Ctrl+C to stop.
echo.

python cli.py worker start --concurrency 4

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Worker exited with code %ERRORLEVEL%.
    echo  Make sure Redis is running (start-redis.bat).
    echo.
    pause
)
endlocal

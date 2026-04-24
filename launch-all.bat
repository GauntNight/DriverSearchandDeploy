@echo off
:: AutoPackager - Launch All Services
:: Starts Redis and the Celery worker in separate windows,
:: then initializes the database if needed.

setlocal

cd /d "%~dp0"

echo.
echo  =============================================
echo   AutoPackager - Launching All Services
echo  =============================================
echo.

:: Activate virtualenv if present
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

:: Initialize the database
echo  [1/3] Initializing database...
python cli.py init
if %ERRORLEVEL% neq 0 (
    echo  [WARNING] Database init returned an error - it may already be initialized.
)
echo.

:: Start Redis in a new window
echo  [2/3] Starting Redis...
start "AutoPackager - Redis" cmd /k "%~dp0start-redis.bat"
:: Give Redis a moment to start
timeout /t 2 /nobreak >nul
echo.

:: Start Celery worker in a new window
echo  [3/3] Starting Celery worker...
start "AutoPackager - Worker" cmd /k "%~dp0start-worker.bat"
echo.

echo  =============================================
echo   All services launched!
echo  =============================================
echo.
echo  Redis and the worker are running in separate windows.
echo  You can now create jobs with:
echo    create-job.bat --vendor dell --model "Latitude 5420"
echo.
echo  Or list existing jobs with:
echo    list-jobs.bat
echo.
pause
endlocal

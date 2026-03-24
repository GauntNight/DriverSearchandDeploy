@echo off
:: AutoPackager - Start Dashboard Server
:: Starts the web dashboard for deployment monitoring.
:: Access at http://localhost:8000

setlocal

cd /d "%~dp0"

:: Activate virtualenv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo.
echo  Starting AutoPackager Dashboard...
echo  Dashboard will be available at http://localhost:8000
echo  Press Ctrl+C to stop.
echo.

uvicorn autopackager.web.api:app --host 0.0.0.0 --port 8000

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Dashboard server exited with code %ERRORLEVEL%.
    echo  Make sure all dependencies are installed (pip install -r requirements.txt).
    echo.
    pause
)
endlocal

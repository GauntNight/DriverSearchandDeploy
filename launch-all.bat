@echo off
echo AutoPackager Launcher
echo =====================
echo Starting Redis in a new window...
start "Redis Server" cmd /k "tools\redis\redis-server.exe redis.conf"
timeout /t 2 /nobreak >nul
echo Starting Celery Worker in a new window...
start "Celery Worker" cmd /k "call .venv\Scripts\activate.bat && python cli.py worker start"
echo.
echo Both services started.
echo To create a driver job, run:
echo   create-job.bat --vendor dell --model "Your Model"
echo.
pause

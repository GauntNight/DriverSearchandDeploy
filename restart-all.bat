@echo off
:: AutoPackager - Restart the full local stack (Redis + Celery worker + dashboard)
:: Stops anything already running, clears the stale Redis dump, relaunches all
:: three detached (logs in data\logs\), then health-checks the ports.
::
::   restart-all.bat              full stop + start
::   restart-all.bat --stop       stop only
::   restart-all.bat --no-worker  start without the Celery worker

setlocal
cd /d "%~dp0"

set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\restart_stack.py %*

endlocal

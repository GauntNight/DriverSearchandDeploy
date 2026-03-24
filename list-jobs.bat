@echo off
:: AutoPackager - List Packaging Jobs
:: Usage:
::   list-jobs.bat               (show all jobs)
::   list-jobs.bat --state failed  (filter by state)
::   list-jobs.bat --limit 50     (show more results)

setlocal

cd /d "%~dp0"

:: Activate virtualenv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

python cli.py jobs list %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Failed to list jobs.
    echo.
    pause
)
endlocal

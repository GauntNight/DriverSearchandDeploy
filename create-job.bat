@echo off
:: AutoPackager - Create a Driver Update Job
:: Usage:
::   create-job.bat --vendor dell --model "Latitude 5420"
::   create-job.bat --vendor hp --model "EliteBook 850 G8"
::   create-job.bat --vendor lenovo --model "ThinkPad X1 Carbon Gen 9"
::
:: Optional flags:
::   --driver-type chipset|network|graphics
::   --current-version "1.0"

setlocal

cd /d "%~dp0"

:: Activate virtualenv if present
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if "%~1"=="" (
    echo.
    echo  AutoPackager - Create Driver Update Job
    echo  ----------------------------------------
    echo  Usage: create-job.bat --vendor ^<dell^|hp^|lenovo^> --model "Model Name"
    echo.
    echo  Examples:
    echo    create-job.bat --vendor dell --model "Latitude 5420"
    echo    create-job.bat --vendor hp --model "EliteBook 850 G8"
    echo    create-job.bat --vendor lenovo --model "ThinkPad X1 Carbon Gen 9" --driver-type chipset
    echo.
    pause
    exit /b 1
)

python cli.py create-driver-job %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Failed to create job. Is Redis running?
    echo.
    pause
)
endlocal

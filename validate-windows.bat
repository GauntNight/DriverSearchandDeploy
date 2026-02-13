@echo off
REM AutoPackager Windows Validation Test
REM This script tests key Windows functionality before running setup

echo ===================================
echo AutoPackager Windows Validation
echo ===================================
echo.

REM Test 1: Check Python
echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   ERROR: Python not found!
    echo   Please install Python 3.9+ from https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during installation
    exit /b 1
)
python --version
echo   OK: Python found
echo.

REM Test 2: Check pip
echo [2/6] Checking pip...
python -m pip --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   ERROR: pip not found!
    exit /b 1
)
echo   OK: pip found
echo.

REM Test 3: Check if running from correct directory
echo [3/6] Checking project directory...
if not exist "requirements.txt" (
    echo   ERROR: requirements.txt not found!
    echo   Please run this script from the AutoPackager root directory
    exit /b 1
)
if not exist "cli.py" (
    echo   ERROR: cli.py not found!
    echo   Please run this script from the AutoPackager root directory
    exit /b 1
)
echo   OK: Project files found
echo.

REM Test 4: Check write permissions
echo [4/6] Checking write permissions...
echo test > .\test_write.tmp 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo   ERROR: No write permissions in current directory!
    echo   Try running PowerShell as Administrator
    exit /b 1
)
del .\test_write.tmp >nul 2>&1
echo   OK: Write permissions verified
echo.

REM Test 5: Test virtual environment creation
echo [5/6] Testing virtual environment creation...
if exist ".\test_venv" (
    rmdir /s /q .\test_venv >nul 2>&1
)
python -m venv test_venv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   ERROR: Failed to create virtual environment!
    echo   Your Python installation may be incomplete
    exit /b 1
)
echo   OK: Virtual environment creation works
rmdir /s /q .\test_venv >nul 2>&1
echo.

REM Test 6: Check PowerShell execution policy
echo [6/6] Checking PowerShell execution policy...
powershell -Command "Get-ExecutionPolicy" > .\exec_policy.tmp
set /p POLICY=<.\exec_policy.tmp
del .\exec_policy.tmp >nul 2>&1
echo   Current policy: %POLICY%
if "%POLICY%"=="Restricted" (
    echo   WARNING: PowerShell execution is restricted!
    echo   You may need to run:
    echo   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
    echo.
)
echo.

echo ===================================
echo Validation Complete!
echo ===================================
echo.
echo Your system is ready for AutoPackager setup!
echo.
echo Next step: Run the setup script:
echo   .\setup.ps1 -UseSQLite
echo.
echo Or if you get execution policy errors:
echo   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -UseSQLite
echo.
pause

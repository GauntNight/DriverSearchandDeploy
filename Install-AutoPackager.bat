@echo off
:: AutoPackager Installer Launcher
:: Double-click this file to install AutoPackager.
:: It will automatically request Administrator rights and bypass the PowerShell
:: execution policy so you don't have to configure anything manually.

setlocal

:: Check if we are already running as Administrator
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  Requesting Administrator privileges...
    echo  A UAC prompt will appear - click Yes to continue.
    echo.
    :: Re-launch this exact batch file elevated using PowerShell
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Running as Administrator - execute the PowerShell installer
echo.
echo  Running AutoPackager installer as Administrator...
echo.

:: Change to the directory containing this batch file
cd /d "%~dp0"

:: Run the PowerShell script with execution policy bypass
:: -NoProfile   : skip user profile (faster, avoids profile conflicts)
:: -ExecutionPolicy Bypass : allow unsigned scripts for this session only
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AutoPackager.ps1" %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Installer exited with code %ERRORLEVEL%.
    echo  Check the output above for details.
    echo.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo  Installation complete. Press any key to close this window.
pause >nul
endlocal

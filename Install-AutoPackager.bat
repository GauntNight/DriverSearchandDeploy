@echo off
:: AutoPackager Installer Launcher
:: Double-click this file to install AutoPackager.
:: It will automatically request Administrator rights and bypass the PowerShell
:: execution policy so you don't have to configure anything manually.
::
:: By default this creates a NEW Azure App Registration and client secret in
:: your tenant - no portal work required. To reuse an existing App
:: Registration, run from a cmd prompt and pass the appropriate switches, e.g.
::   Install-AutoPackager.bat -UseExistingAppRegistration -TenantId "<tid>" ^
::                            -ClientId "<cid>" -ClientSecret "<secret>"
:: Any arguments after the .bat name are forwarded verbatim to
:: Install-AutoPackager.ps1. See SETUP.md for the full switch reference.

setlocal EnableDelayedExpansion

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

:: Verify the PowerShell installer script exists alongside this batch file
if not exist "%~dp0Install-AutoPackager.ps1" (
    echo.
    echo  [ERROR] Install-AutoPackager.ps1 not found in "%~dp0".
    echo  Ensure the PowerShell script is in the same folder as this batch file.
    echo.
    pause
    exit /b 1
)

:: Run the PowerShell script with execution policy bypass
:: -NoProfile   : skip user profile (faster, avoids profile conflicts)
:: -ExecutionPolicy Bypass : allow unsigned scripts for this session only
echo  [DEBUG] Script: "%~dp0Install-AutoPackager.ps1"
echo  [DEBUG] Arguments: %*
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AutoPackager.ps1" %* 2>"%TEMP%\autopackager-ps-stderr.tmp"
set PS_EXIT=%ERRORLEVEL%

:: Check for Group Policy blocking ExecutionPolicy Bypass
if exist "%TEMP%\autopackager-ps-stderr.tmp" (
    findstr /i /c:"AuthorizationManager" /c:"is not digitally signed" /c:"cannot be loaded because running scripts is disabled" "%TEMP%\autopackager-ps-stderr.tmp" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo.
        echo  [ERROR] Group Policy is blocking PowerShell script execution.
        echo  ExecutionPolicy Bypass was denied. Contact your system administrator
        echo  to allow script execution, or run the following in an elevated prompt:
        echo    Set-ExecutionPolicy RemoteSigned -Scope LocalMachine
        echo.
        del "%TEMP%\autopackager-ps-stderr.tmp" >nul 2>&1
        pause
        exit /b 1
    )
    :: Print any other stderr output then clean up
    type "%TEMP%\autopackager-ps-stderr.tmp" 1>&2
    del "%TEMP%\autopackager-ps-stderr.tmp" >nul 2>&1
)

if %PS_EXIT% neq 0 (
    echo.
    echo  [ERROR] Installer exited with code %PS_EXIT%.
    if %PS_EXIT% equ 1 (
        echo  General error - check the output above for details.
    ) else if %PS_EXIT% equ 2 (
        echo  Incorrect usage or invalid arguments were provided.
    ) else if %PS_EXIT% equ 5 (
        echo  Access denied - ensure you are running as Administrator.
    ) else if %PS_EXIT% equ 87 (
        echo  Invalid parameter - check the arguments passed to the script.
    ) else (
        echo  Check the output above for details.
    )
    echo.
    pause
    exit /b %PS_EXIT%
)

echo.
echo  Installation complete. Press any key to close this window.
pause >nul
endlocal

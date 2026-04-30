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

:: Help: print usage and exit before doing anything (no elevation, no PS launch)
if /i "%~1"=="/?"     goto :show_help
if /i "%~1"=="-?"     goto :show_help
if /i "%~1"=="/help"  goto :show_help
if /i "%~1"=="-h"     goto :show_help
if /i "%~1"=="--help" goto :show_help

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
goto :eof

:show_help
echo.
echo  AutoPackager Installer (Install-AutoPackager.bat)
echo  -------------------------------------------------
echo.
echo  Usage:
echo    Install-AutoPackager.bat [switches forwarded to Install-AutoPackager.ps1]
echo.
echo  Default behaviour (no switches):
echo    - Installs Python, Git, Redis, IntuneWinAppUtil, the venv and deps.
echo    - Signs in to Azure (one browser prompt).
echo    - CREATES A NEW App Registration ^("AutoPackager-ServicePrincipal"^)
echo      and a fresh client secret in your tenant. No portal work needed.
echo    - Adds Microsoft Graph permissions and grants tenant-wide consent.
echo    - Creates the four Entra ID deployment ring groups.
echo    - Writes a complete .env file.
echo.
echo  Common switches (forwarded to Install-AutoPackager.ps1):
echo    -SkipAzure                       Skip the Azure step entirely.
echo    -SkipPython                      Assume Python 3.9+ is installed.
echo    -UseSQLite:$false                Configure PostgreSQL instead of SQLite.
echo    -LlmProvider ^<openai^|anthropic^>  Choose LLM provider ^(default: openai^).
echo    -LlmApiKey "^<key^>"               Pre-supply the LLM API key.
echo    -TenantId "^<tid^>"                Pre-supply the Azure Tenant ID.
echo    -AppName "^<name^>"                Display name for the new App Registration.
echo    -CreateAppRegistration:$false    Reuse an existing App Registration.
echo    -UseExistingAppRegistration      Same as -CreateAppRegistration:$false.
echo    -ClientId "^<cid^>"                Existing App Registration Client ID.
echo    -ClientSecret "^<secret^>"         Existing client secret value.
echo.
echo  Examples:
echo    :: Default - new App Registration + secret created automatically
echo    Install-AutoPackager.bat
echo.
echo    :: Pre-supply tenant for an unattended Azure step
echo    Install-AutoPackager.bat -TenantId "00000000-0000-0000-0000-000000000000"
echo.
echo    :: Reuse an existing App Registration (BYO credentials)
echo    Install-AutoPackager.bat -UseExistingAppRegistration ^^
echo                             -TenantId "^<tid^>" -ClientId "^<cid^>" -ClientSecret "^<secret^>"
echo.
echo    :: Local-only install; configure Azure later via .\azure-setup.ps1
echo    Install-AutoPackager.bat -SkipAzure
echo.
echo  Help options: /? , -? , /help , -h , --help
echo  Full reference: SETUP.md ^(Windows Installer Quick Reference^).
echo.
endlocal
goto :eof

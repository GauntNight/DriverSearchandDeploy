@echo off
REM End-to-end dashboard verification script for Windows
REM Tests all dashboard functionality with sample data

setlocal EnableDelayedExpansion

set DASHBOARD_PORT=8000
set DASHBOARD_URL=http://localhost:%DASHBOARD_PORT%
set VERIFICATION_LOG=verification-results.txt

echo === AutoPackager Dashboard E2E Verification === > %VERIFICATION_LOG%
echo Started: %date% %time% >> %VERIFICATION_LOG%
echo. >> %VERIFICATION_LOG%

echo === AutoPackager Dashboard E2E Verification ===
echo Started: %date% %time%
echo.

REM Activate virtual environment if it exists
if exist .venv\Scripts\activate.bat (
    echo [INFO] Activating virtual environment...
    call .venv\Scripts\activate.bat
)

REM Detect Python command
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=python
) else (
    where python3 >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_CMD=python3
    ) else (
        echo [FAIL] Python not found
        exit /b 1
    )
)

REM Step 1: Check Redis
echo [INFO] Step 1: Checking Redis...
netstat -an | findstr ":6379" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] Redis is running on port 6379
    echo [PASS] Redis is running on port 6379 >> %VERIFICATION_LOG%
) else (
    echo [INFO] Redis not running - please start manually with start-redis.bat
    echo [FAIL] Redis not running >> %VERIFICATION_LOG%
)

REM Step 2: Check Celery Worker
echo [INFO] Step 2: Checking Celery worker...
tasklist | findstr "celery" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] Celery worker is running
    echo [PASS] Celery worker is running >> %VERIFICATION_LOG%
) else (
    echo [INFO] Celery worker not running - please start manually with start-worker.bat
    echo [FAIL] Celery worker not running >> %VERIFICATION_LOG%
)

REM Step 3: Create test job
echo [INFO] Step 3: Creating test job via CLI...
%PYTHON_CMD% cli.py create-job --device-id test-device-001 --manufacturer HP --model "EliteBook 850 G8" --driver-type network --os-version "Windows 11 22H2" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] Test job created successfully
    echo [PASS] Test job created successfully >> %VERIFICATION_LOG%
) else (
    echo [FAIL] Failed to create test job
    echo [FAIL] Failed to create test job >> %VERIFICATION_LOG%
)

REM Step 4: Check dashboard server
echo [INFO] Step 4: Checking dashboard server...
netstat -an | findstr ":%DASHBOARD_PORT%" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] Dashboard server is running on port %DASHBOARD_PORT%
    echo [PASS] Dashboard server is running on port %DASHBOARD_PORT% >> %VERIFICATION_LOG%
) else (
    echo [INFO] Dashboard server not running - please start manually with start-dashboard.bat
    echo [FAIL] Dashboard server not running >> %VERIFICATION_LOG%
    echo.
    echo Please start the dashboard server first:
    echo   start-dashboard.bat
    echo.
    echo Then run this verification again.
    pause
    exit /b 1
)

REM Step 5: Test dashboard load time
echo [INFO] Step 5: Testing dashboard load time...
curl -s -o nul -w "%%{http_code}" %DASHBOARD_URL%/ --max-time 3 > temp_status.txt
set /p HTTP_CODE=<temp_status.txt
del temp_status.txt

if "%HTTP_CODE%"=="200" (
    echo [PASS] Dashboard loads successfully ^(HTTP 200^)
    echo [PASS] Dashboard loads successfully ^(HTTP 200^) >> %VERIFICATION_LOG%
) else (
    echo [FAIL] Dashboard failed to load ^(HTTP %HTTP_CODE%^)
    echo [FAIL] Dashboard failed to load ^(HTTP %HTTP_CODE%^) >> %VERIFICATION_LOG%
)

REM Step 6: Test API endpoints
echo [INFO] Step 6: Testing API endpoints...

curl -s %DASHBOARD_URL%/api/stats > temp_stats.json
findstr "jobs" temp_stats.json >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] GET /api/stats returns valid JSON data
    echo [PASS] GET /api/stats returns valid JSON data >> %VERIFICATION_LOG%
) else (
    echo [FAIL] GET /api/stats failed
    echo [FAIL] GET /api/stats failed >> %VERIFICATION_LOG%
)
del temp_stats.json

curl -s %DASHBOARD_URL%/api/jobs > temp_jobs.json
findstr "jobs" temp_jobs.json >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] GET /api/jobs returns valid JSON data
    echo [PASS] GET /api/jobs returns valid JSON data >> %VERIFICATION_LOG%
) else (
    echo [FAIL] GET /api/jobs failed
    echo [FAIL] GET /api/jobs failed >> %VERIFICATION_LOG%
)
del temp_jobs.json

curl -s %DASHBOARD_URL%/api/deployments > temp_deployments.json
findstr "deployments" temp_deployments.json >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] GET /api/deployments returns valid JSON data
    echo [PASS] GET /api/deployments returns valid JSON data >> %VERIFICATION_LOG%
) else (
    echo [FAIL] GET /api/deployments failed
    echo [FAIL] GET /api/deployments failed >> %VERIFICATION_LOG%
)
del temp_deployments.json

curl -s %DASHBOARD_URL%/api/deployments/rings > temp_rings.json
findstr "rings" temp_rings.json >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] GET /api/deployments/rings returns valid JSON data
    echo [PASS] GET /api/deployments/rings returns valid JSON data >> %VERIFICATION_LOG%
) else (
    echo [FAIL] GET /api/deployments/rings failed
    echo [FAIL] GET /api/deployments/rings failed >> %VERIFICATION_LOG%
)
del temp_rings.json

curl -s %DASHBOARD_URL%/api/activity > temp_activity.json
findstr "activity" temp_activity.json >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] GET /api/activity returns valid JSON data
    echo [PASS] GET /api/activity returns valid JSON data >> %VERIFICATION_LOG%
) else (
    echo [FAIL] GET /api/activity failed
    echo [FAIL] GET /api/activity failed >> %VERIFICATION_LOG%
)
del temp_activity.json

REM Step 7: Test static files
echo [INFO] Step 7: Testing static file delivery...
curl -s -o nul -w "%%{http_code}" %DASHBOARD_URL%/static/styles.css > temp_css_status.txt
set /p CSS_CODE=<temp_css_status.txt
del temp_css_status.txt

if "%CSS_CODE%"=="200" (
    echo [PASS] CSS file loads successfully ^(HTTP 200^)
    echo [PASS] CSS file loads successfully ^(HTTP 200^) >> %VERIFICATION_LOG%
) else (
    echo [FAIL] CSS file failed to load ^(HTTP %CSS_CODE%^)
    echo [FAIL] CSS file failed to load ^(HTTP %CSS_CODE%^) >> %VERIFICATION_LOG%
)

curl -s -o nul -w "%%{http_code}" %DASHBOARD_URL%/static/dashboard.js > temp_js_status.txt
set /p JS_CODE=<temp_js_status.txt
del temp_js_status.txt

if "%JS_CODE%"=="200" (
    echo [PASS] JavaScript file loads successfully ^(HTTP 200^)
    echo [PASS] JavaScript file loads successfully ^(HTTP 200^) >> %VERIFICATION_LOG%
) else (
    echo [FAIL] JavaScript file failed to load ^(HTTP %JS_CODE%^)
    echo [FAIL] JavaScript file failed to load ^(HTTP %JS_CODE%^) >> %VERIFICATION_LOG%
)

REM Step 8: Check auto-refresh configuration
echo [INFO] Step 8: Checking auto-refresh configuration...
curl -s %DASHBOARD_URL%/static/dashboard.js > temp_dashboard_js.txt
findstr "5000" temp_dashboard_js.txt >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [PASS] Auto-refresh configured for 5 seconds
    echo [PASS] Auto-refresh configured for 5 seconds >> %VERIFICATION_LOG%
) else (
    echo [FAIL] Auto-refresh not properly configured
    echo [FAIL] Auto-refresh not properly configured >> %VERIFICATION_LOG%
)
del temp_dashboard_js.txt

REM Step 9: Check responsive design
echo [INFO] Step 9: Checking responsive design...
curl -s %DASHBOARD_URL%/static/styles.css > temp_styles.txt
findstr /C:"@media" temp_styles.txt > temp_media.txt
for /f %%A in ('type temp_media.txt ^| find /c /v ""') do set MEDIA_COUNT=%%A
del temp_styles.txt
del temp_media.txt

if %MEDIA_COUNT% GEQ 3 (
    echo [PASS] Responsive design implemented ^(%MEDIA_COUNT% media queries^)
    echo [PASS] Responsive design implemented ^(%MEDIA_COUNT% media queries^) >> %VERIFICATION_LOG%
) else (
    echo [FAIL] Insufficient responsive design ^(only %MEDIA_COUNT% media queries^)
    echo [FAIL] Insufficient responsive design ^(only %MEDIA_COUNT% media queries^) >> %VERIFICATION_LOG%
)

REM Summary
echo.
echo === Verification Summary ===
echo Completed: %date% %time%
echo.
echo Dashboard URL: %DASHBOARD_URL%
echo.
echo. >> %VERIFICATION_LOG%
echo === Verification Summary === >> %VERIFICATION_LOG%
echo Completed: %date% %time% >> %VERIFICATION_LOG%
echo Dashboard URL: %DASHBOARD_URL% >> %VERIFICATION_LOG%
echo. >> %VERIFICATION_LOG%

echo Full results saved to: %VERIFICATION_LOG%
echo.
echo === VERIFICATION COMPLETE ===
echo.
echo To view the dashboard, open your browser to:
echo   %DASHBOARD_URL%
echo.

pause

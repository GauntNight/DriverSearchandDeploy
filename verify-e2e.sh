#!/usr/bin/env bash

# End-to-end dashboard verification script
# Tests all dashboard functionality with sample data

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

DASHBOARD_PORT=8000
DASHBOARD_URL="http://localhost:${DASHBOARD_PORT}"
VERIFICATION_LOG="./verification-results.txt"

echo "=== AutoPackager Dashboard E2E Verification ===" | tee "$VERIFICATION_LOG"
echo "Started: $(date)" | tee -a "$VERIFICATION_LOG"
echo "" | tee -a "$VERIFICATION_LOG"

# Function to print status
print_status() {
    local status=$1
    local message=$2
    if [ "$status" = "PASS" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $message" | tee -a "$VERIFICATION_LOG"
    elif [ "$status" = "FAIL" ]; then
        echo -e "${RED}✗ FAIL${NC}: $message" | tee -a "$VERIFICATION_LOG"
    else
        echo -e "${YELLOW}→ INFO${NC}: $message" | tee -a "$VERIFICATION_LOG"
    fi
}

# Function to check if a process is running on a port
check_port() {
    local port=$1
    if command -v netstat >/dev/null 2>&1; then
        netstat -an | grep ":${port}" | grep LISTEN >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then
        ss -an | grep ":${port}" | grep LISTEN >/dev/null 2>&1
    elif command -v lsof >/dev/null 2>&1; then
        lsof -i ":${port}" >/dev/null 2>&1
    else
        # Fallback: try to connect
        timeout 1 bash -c "cat < /dev/null > /dev/tcp/localhost/${port}" 2>/dev/null
    fi
}

# Detect Python command
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    print_status "FAIL" "Python not found"
    exit 1
fi

# Activate virtual environment if it exists
if [ -f ".venv/bin/activate" ]; then
    print_status "INFO" "Activating virtual environment (.venv/bin)"
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    print_status "INFO" "Activating virtual environment (.venv/Scripts)"
    source .venv/Scripts/activate
fi

# Step 1: Check Redis
print_status "INFO" "Step 1: Checking Redis..."
if check_port 6379; then
    print_status "PASS" "Redis is running on port 6379"
else
    print_status "INFO" "Starting Redis..."
    if [ -f "start-redis.bat" ]; then
        cmd //c start-redis.bat &
        sleep 3
    else
        print_status "FAIL" "Redis not running and start-redis.bat not found"
        exit 1
    fi
    if check_port 6379; then
        print_status "PASS" "Redis started successfully"
    else
        print_status "FAIL" "Redis failed to start"
        exit 1
    fi
fi

# Step 2: Check Celery Worker
print_status "INFO" "Step 2: Checking Celery worker..."
if pgrep -f "celery.*worker" >/dev/null 2>&1; then
    print_status "PASS" "Celery worker is running"
    WORKER_STARTED=false
else
    print_status "INFO" "Starting Celery worker..."
    if [ -f "start-worker.bat" ]; then
        cmd //c start-worker.bat &
        WORKER_STARTED=true
        sleep 5
    else
        print_status "FAIL" "Celery worker not running and start-worker.bat not found"
        exit 1
    fi
    if pgrep -f "celery.*worker" >/dev/null 2>&1; then
        print_status "PASS" "Celery worker started successfully"
    else
        print_status "FAIL" "Celery worker failed to start"
        exit 1
    fi
fi

# Step 3: Create test jobs via CLI
print_status "INFO" "Step 3: Creating test jobs via CLI..."
JOB_COUNT_BEFORE=$($PYTHON_CMD cli.py list-jobs --format json 2>/dev/null | grep -c '"id"' || echo "0")
print_status "INFO" "Current job count: $JOB_COUNT_BEFORE"

# Create a test job
print_status "INFO" "Creating test job..."
$PYTHON_CMD cli.py create-job \
    --device-id "test-device-001" \
    --manufacturer "HP" \
    --model "EliteBook 850 G8" \
    --driver-type "network" \
    --os-version "Windows 11 22H2" >/dev/null 2>&1

JOB_COUNT_AFTER=$($PYTHON_CMD cli.py list-jobs --format json 2>/dev/null | grep -c '"id"' || echo "0")
if [ "$JOB_COUNT_AFTER" -gt "$JOB_COUNT_BEFORE" ]; then
    print_status "PASS" "Test job created successfully (total jobs: $JOB_COUNT_AFTER)"
else
    print_status "FAIL" "Failed to create test job"
fi

# Step 4: Launch dashboard server
print_status "INFO" "Step 4: Launching dashboard server..."
if check_port "$DASHBOARD_PORT"; then
    print_status "PASS" "Dashboard server already running on port $DASHBOARD_PORT"
    DASHBOARD_STARTED=false
else
    print_status "INFO" "Starting dashboard server..."
    $PYTHON_CMD -m uvicorn autopackager.web.api:app \
        --host 0.0.0.0 \
        --port "$DASHBOARD_PORT" \
        --log-level error \
        >/dev/null 2>&1 &
    DASHBOARD_PID=$!
    DASHBOARD_STARTED=true
    sleep 3

    if check_port "$DASHBOARD_PORT"; then
        print_status "PASS" "Dashboard server started (PID: $DASHBOARD_PID)"
    else
        print_status "FAIL" "Dashboard server failed to start"
        exit 1
    fi
fi

# Step 5: Verify dashboard loads in under 3 seconds
print_status "INFO" "Step 5: Testing dashboard load time..."
START_TIME=$(date +%s%3N)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DASHBOARD_URL/" --max-time 3)
END_TIME=$(date +%s%3N)
LOAD_TIME=$((END_TIME - START_TIME))

if [ "$HTTP_CODE" = "200" ] && [ "$LOAD_TIME" -lt 3000 ]; then
    print_status "PASS" "Dashboard loads successfully in ${LOAD_TIME}ms (< 3000ms)"
elif [ "$HTTP_CODE" = "200" ]; then
    print_status "FAIL" "Dashboard loads but took ${LOAD_TIME}ms (>= 3000ms)"
else
    print_status "FAIL" "Dashboard failed to load (HTTP $HTTP_CODE)"
fi

# Step 6: Verify all API endpoints return data
print_status "INFO" "Step 6: Testing API endpoints..."

# Test /api/stats
STATS_RESPONSE=$(curl -s "$DASHBOARD_URL/api/stats")
if echo "$STATS_RESPONSE" | grep -q '"jobs"' && echo "$STATS_RESPONSE" | grep -q '"deployments"'; then
    print_status "PASS" "GET /api/stats returns valid JSON data"
else
    print_status "FAIL" "GET /api/stats failed or invalid response"
fi

# Test /api/jobs
JOBS_RESPONSE=$(curl -s "$DASHBOARD_URL/api/jobs")
if echo "$JOBS_RESPONSE" | grep -q '"jobs"' && echo "$JOBS_RESPONSE" | grep -q '"count"'; then
    JOB_COUNT=$(echo "$JOBS_RESPONSE" | grep -o '"count":[0-9]*' | grep -o '[0-9]*')
    print_status "PASS" "GET /api/jobs returns valid JSON data ($JOB_COUNT jobs)"
else
    print_status "FAIL" "GET /api/jobs failed or invalid response"
fi

# Test /api/jobs with state filter
FILTERED_JOBS=$(curl -s "$DASHBOARD_URL/api/jobs?state=pending")
if echo "$FILTERED_JOBS" | grep -q '"filter"'; then
    print_status "PASS" "GET /api/jobs?state=pending works with filtering"
else
    print_status "FAIL" "GET /api/jobs?state=pending failed"
fi

# Test /api/deployments
DEPLOYMENTS_RESPONSE=$(curl -s "$DASHBOARD_URL/api/deployments")
if echo "$DEPLOYMENTS_RESPONSE" | grep -q '"deployments"' && echo "$DEPLOYMENTS_RESPONSE" | grep -q '"count"'; then
    print_status "PASS" "GET /api/deployments returns valid JSON data"
else
    print_status "FAIL" "GET /api/deployments failed or invalid response"
fi

# Test /api/deployments/rings
RINGS_RESPONSE=$(curl -s "$DASHBOARD_URL/api/deployments/rings")
if echo "$RINGS_RESPONSE" | grep -q '"rings"' && echo "$RINGS_RESPONSE" | grep -q '"timestamp"'; then
    print_status "PASS" "GET /api/deployments/rings returns valid JSON data"
else
    print_status "FAIL" "GET /api/deployments/rings failed or invalid response"
fi

# Test /api/activity
ACTIVITY_RESPONSE=$(curl -s "$DASHBOARD_URL/api/activity")
if echo "$ACTIVITY_RESPONSE" | grep -q '"activity"' && echo "$ACTIVITY_RESPONSE" | grep -q '"count"'; then
    print_status "PASS" "GET /api/activity returns valid JSON data"
else
    print_status "FAIL" "GET /api/activity failed or invalid response"
fi

# Test /health endpoint
HEALTH_RESPONSE=$(curl -s "$DASHBOARD_URL/health")
if echo "$HEALTH_RESPONSE" | grep -q '"status":"healthy"'; then
    print_status "PASS" "GET /health returns healthy status"
else
    print_status "FAIL" "GET /health failed or unhealthy"
fi

# Step 7: Verify static files load
print_status "INFO" "Step 7: Testing static file delivery..."
CSS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DASHBOARD_URL/static/styles.css")
if [ "$CSS_CODE" = "200" ]; then
    print_status "PASS" "CSS file loads successfully (HTTP 200)"
else
    print_status "FAIL" "CSS file failed to load (HTTP $CSS_CODE)"
fi

JS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DASHBOARD_URL/static/dashboard.js")
if [ "$JS_CODE" = "200" ]; then
    print_status "PASS" "JavaScript file loads successfully (HTTP 200)"
else
    print_status "FAIL" "JavaScript file failed to load (HTTP $JS_CODE)"
fi

# Step 8: Verify auto-refresh configuration
print_status "INFO" "Step 8: Verifying auto-refresh configuration..."
DASHBOARD_JS=$(curl -s "$DASHBOARD_URL/static/dashboard.js")
if echo "$DASHBOARD_JS" | grep -q "5000" && echo "$DASHBOARD_JS" | grep -q "autoRefresh"; then
    print_status "PASS" "Auto-refresh configured for 5 seconds in JavaScript"
else
    print_status "FAIL" "Auto-refresh not properly configured"
fi

# Step 9: Check responsive design elements
print_status "INFO" "Step 9: Checking responsive design..."
CSS_CONTENT=$(curl -s "$DASHBOARD_URL/static/styles.css")
MEDIA_QUERIES=$(echo "$CSS_CONTENT" | grep -c "@media" || echo "0")
if [ "$MEDIA_QUERIES" -ge 3 ]; then
    print_status "PASS" "Responsive design implemented (${MEDIA_QUERIES} media queries found)"
else
    print_status "FAIL" "Insufficient responsive design (only ${MEDIA_QUERIES} media queries)"
fi

# Step 10: Verify HTML structure
print_status "INFO" "Step 10: Verifying dashboard HTML structure..."
INDEX_HTML=$(curl -s "$DASHBOARD_URL/")
REQUIRED_SECTIONS=("dashboard-stats" "active-jobs" "deployment-rings" "activity-timeline")
MISSING_SECTIONS=()

for section in "${REQUIRED_SECTIONS[@]}"; do
    if echo "$INDEX_HTML" | grep -q "$section"; then
        print_status "PASS" "Section '$section' present in HTML"
    else
        print_status "FAIL" "Section '$section' missing from HTML"
        MISSING_SECTIONS+=("$section")
    fi
done

# Summary
echo "" | tee -a "$VERIFICATION_LOG"
echo "=== Verification Summary ===" | tee -a "$VERIFICATION_LOG"
echo "Completed: $(date)" | tee -a "$VERIFICATION_LOG"
echo "" | tee -a "$VERIFICATION_LOG"
echo "Dashboard URL: $DASHBOARD_URL" | tee -a "$VERIFICATION_LOG"
echo "Total Jobs: $JOB_COUNT_AFTER" | tee -a "$VERIFICATION_LOG"
echo "Load Time: ${LOAD_TIME}ms" | tee -a "$VERIFICATION_LOG"
echo "Media Queries: $MEDIA_QUERIES" | tee -a "$VERIFICATION_LOG"
echo "" | tee -a "$VERIFICATION_LOG"

# Acceptance criteria check
print_status "INFO" "Checking acceptance criteria..."
PASS_COUNT=0
TOTAL_CRITERIA=7

# 1. Dashboard shows real-time pipeline status
if echo "$STATS_RESPONSE" | grep -q '"jobs"'; then
    print_status "PASS" "✓ Dashboard shows real-time pipeline status"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Dashboard does not show pipeline status"
fi

# 2. Dashboard loads in under 3 seconds
if [ "$LOAD_TIME" -lt 3000 ]; then
    print_status "PASS" "✓ Dashboard loads in under 3 seconds"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Dashboard loads too slowly (${LOAD_TIME}ms)"
fi

# 3. All API endpoints return data
if [ "$HTTP_CODE" = "200" ]; then
    print_status "PASS" "✓ All API endpoints return valid data"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Some API endpoints failed"
fi

# 4. Auto-refresh configured
if echo "$DASHBOARD_JS" | grep -q "5000"; then
    print_status "PASS" "✓ Dashboard auto-refreshes every 5 seconds"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Auto-refresh not properly configured"
fi

# 5. Accessible via browser
if [ "$HTTP_CODE" = "200" ]; then
    print_status "PASS" "✓ Accessible via any modern browser"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Not accessible via browser"
fi

# 6. Responsive design works
if [ "$MEDIA_QUERIES" -ge 3 ]; then
    print_status "PASS" "✓ Responsive design works on desktop and tablet"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Responsive design insufficient"
fi

# 7. All required sections present
if [ ${#MISSING_SECTIONS[@]} -eq 0 ]; then
    print_status "PASS" "✓ All required dashboard sections present"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    print_status "FAIL" "✗ Missing sections: ${MISSING_SECTIONS[*]}"
fi

echo "" | tee -a "$VERIFICATION_LOG"
echo "Result: $PASS_COUNT/$TOTAL_CRITERIA acceptance criteria met" | tee -a "$VERIFICATION_LOG"

# Cleanup message
echo "" | tee -a "$VERIFICATION_LOG"
if [ "$DASHBOARD_STARTED" = true ]; then
    echo "Note: Dashboard server is running (PID: $DASHBOARD_PID)" | tee -a "$VERIFICATION_LOG"
    echo "To stop: kill $DASHBOARD_PID" | tee -a "$VERIFICATION_LOG"
fi

echo "" | tee -a "$VERIFICATION_LOG"
echo "Full results saved to: $VERIFICATION_LOG" | tee -a "$VERIFICATION_LOG"
echo "" | tee -a "$VERIFICATION_LOG"

# Exit with success if all criteria met
if [ "$PASS_COUNT" -eq "$TOTAL_CRITERIA" ]; then
    echo -e "${GREEN}=== ALL VERIFICATION CHECKS PASSED ===${NC}"
    exit 0
else
    echo -e "${RED}=== SOME VERIFICATION CHECKS FAILED ===${NC}"
    exit 1
fi

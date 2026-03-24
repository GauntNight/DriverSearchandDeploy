#!/usr/bin/env bash
# Quick verification of dashboard on port 8001

PORT=8001
URL="http://localhost:${PORT}"

echo "=== Dashboard E2E Verification ==="
echo ""
echo "Testing on $URL"
echo ""

# Test 1: Dashboard loads
echo "1. Testing dashboard homepage..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL/" --max-time 3)
if [ "$HTTP_CODE" = "200" ]; then
    echo "   ✓ PASS: Dashboard loads (HTTP 200)"
else
    echo "   ✗ FAIL: Dashboard returned HTTP $HTTP_CODE"
fi

# Test 2: API Stats
echo "2. Testing /api/stats..."
if curl -s "$URL/api/stats" | grep -q '"jobs"'; then
    echo "   ✓ PASS: /api/stats returns data"
else
    echo "   ✗ FAIL: /api/stats failed"
fi

# Test 3: API Jobs
echo "3. Testing /api/jobs..."
if curl -s "$URL/api/jobs" | grep -q '"jobs"'; then
    echo "   ✓ PASS: /api/jobs returns data"
else
    echo "   ✗ FAIL: /api/jobs failed"
fi

# Test 4: API Deployments
echo "4. Testing /api/deployments..."
if curl -s "$URL/api/deployments" | grep -q '"deployments"'; then
    echo "   ✓ PASS: /api/deployments returns data"
else
    echo "   ✗ FAIL: /api/deployments failed"
fi

# Test 5: API Deployment Rings
echo "5. Testing /api/deployments/rings..."
if curl -s "$URL/api/deployments/rings" | grep -q '"rings"'; then
    echo "   ✓ PASS: /api/deployments/rings returns data"
else
    echo "   ✗ FAIL: /api/deployments/rings failed"
fi

# Test 6: API Activity
echo "6. Testing /api/activity..."
if curl -s "$URL/api/activity" | grep -q '"activity"'; then
    echo "   ✓ PASS: /api/activity returns data"
else
    echo "   ✗ FAIL: /api/activity failed"
fi

# Test 7: Static CSS
echo "7. Testing static/styles.css..."
CSS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL/static/styles.css")
if [ "$CSS_CODE" = "200" ]; then
    echo "   ✓ PASS: CSS loads (HTTP 200)"
else
    echo "   ✗ FAIL: CSS returned HTTP $CSS_CODE"
fi

# Test 8: Static JavaScript
echo "8. Testing static/dashboard.js..."
JS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL/static/dashboard.js")
if [ "$JS_CODE" = "200" ]; then
    echo "   ✓ PASS: JavaScript loads (HTTP 200)"
else
    echo "   ✗ FAIL: JavaScript returned HTTP $JS_CODE"
fi

# Test 9: Auto-refresh configuration
echo "9. Testing auto-refresh configuration..."
if curl -s "$URL/static/dashboard.js" | grep -q "5000"; then
    echo "   ✓ PASS: Auto-refresh configured (5000ms)"
else
    echo "   ✗ FAIL: Auto-refresh not found"
fi

# Test 10: Responsive design
echo "10. Testing responsive design..."
MEDIA_COUNT=$(curl -s "$URL/static/styles.css" | grep -c "@media" || echo "0")
if [ "$MEDIA_COUNT" -ge 3 ]; then
    echo "   ✓ PASS: Responsive design ($MEDIA_COUNT media queries)"
else
    echo "   ✗ FAIL: Insufficient media queries ($MEDIA_COUNT)"
fi

echo ""
echo "=== Verification Complete ==="
echo ""
echo "Dashboard is accessible at: $URL"

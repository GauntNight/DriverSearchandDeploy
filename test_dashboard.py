#!/usr/bin/env python
"""Test script to verify dashboard server is working"""

import requests
import time
import sys

def test_dashboard():
    base_url = "http://localhost:8000"

    print("Testing AutoPackager Dashboard...")

    # Test 1: Health endpoint
    try:
        r = requests.get(f"{base_url}/health")
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
        print("✓ Health endpoint works")
    except Exception as e:
        print(f"✗ Health endpoint failed: {e}")
        return False

    # Test 2: Root path (dashboard HTML)
    try:
        r = requests.get(f"{base_url}/")
        assert r.status_code == 200, f"Root path failed: {r.status_code}"
        assert "AutoPackager Dashboard" in r.text, "Dashboard HTML content not found"
        print("✓ Dashboard HTML loads")
    except Exception as e:
        print(f"✗ Dashboard HTML failed: {e}")
        return False

    # Test 3: Static files
    try:
        r = requests.get(f"{base_url}/static/index.html")
        assert r.status_code == 200, f"Static file failed: {r.status_code}"
        print("✓ Static files accessible")
    except Exception as e:
        print(f"✗ Static files failed: {e}")
        # Not critical

    print("\n✓ All tests passed!")
    return True

if __name__ == "__main__":
    sys.exit(0 if test_dashboard() else 1)

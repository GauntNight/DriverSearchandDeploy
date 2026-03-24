#!/bin/bash
# AutoPackager Dashboard Server Launch Script
# This script starts the web dashboard server for deployment monitoring
# Run as: ./start-dashboard.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
HOST="${DASHBOARD_HOST:-0.0.0.0}"
PORT="${DASHBOARD_PORT:-8000}"
WORKERS="${DASHBOARD_WORKERS:-1}"

echo -e "${CYAN}==================================${NC}"
echo -e "${CYAN}AutoPackager Dashboard Server${NC}"
echo -e "${CYAN}==================================${NC}"
echo ""

# Check Prerequisites
echo -e "${YELLOW}[1/3] Checking prerequisites...${NC}"

# Check Python (try python3 first, then python)
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo -e "${RED}ERROR: Python not found!${NC}"
    echo "Please install Python 3.9+:"
    echo "  Ubuntu/Debian: sudo apt-get install python3"
    echo "  MacOS: brew install python3"
    echo "  Windows: Download from python.org"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version)
echo -e "  ${GREEN}Found: $PYTHON_VERSION${NC}"

# Check for virtual environment
if [ ! -d ".venv" ]; then
    echo -e "${RED}ERROR: Virtual environment not found!${NC}"
    echo "Please run setup.sh first to create the virtual environment."
    exit 1
fi

echo -e "  ${GREEN}Virtual environment: OK${NC}"

echo ""

# Activate Virtual Environment
echo -e "${YELLOW}[2/3] Activating virtual environment...${NC}"

# Detect OS and activate accordingly
if [ -f ".venv/Scripts/activate" ]; then
    # Windows (Git Bash / WSL)
    source .venv/Scripts/activate
elif [ -f ".venv/bin/activate" ]; then
    # Linux / macOS
    source .venv/bin/activate
else
    echo -e "${RED}ERROR: Virtual environment activation script not found${NC}"
    exit 1
fi

if [ $? -eq 0 ]; then
    echo -e "  ${GREEN}Virtual environment activated${NC}"
else
    echo -e "${RED}ERROR: Failed to activate virtual environment${NC}"
    exit 1
fi

echo ""

# Start Dashboard Server
echo -e "${YELLOW}[3/3] Starting dashboard server...${NC}"
echo -e "  ${CYAN}Host: $HOST${NC}"
echo -e "  ${CYAN}Port: $PORT${NC}"
echo -e "  ${CYAN}Workers: $WORKERS${NC}"
echo ""
echo -e "${GREEN}Dashboard will be available at: http://localhost:$PORT${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""

# Start uvicorn server using Python module (more reliable cross-platform)
$PYTHON_CMD -m uvicorn autopackager.web.api:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level info

# If we get here, the server has stopped
echo ""
echo -e "${YELLOW}Dashboard server stopped${NC}"

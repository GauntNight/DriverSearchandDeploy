#!/bin/bash
# AutoPackager Quick Setup Script for Linux/WSL/Mac
# This script automates the installation and configuration of AutoPackager
# Run as: ./setup.sh

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Options
USE_SQLITE=false
SKIP_REDIS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --sqlite)
            USE_SQLITE=true
            shift
            ;;
        --skip-redis)
            SKIP_REDIS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./setup.sh [--sqlite] [--skip-redis]"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}==================================${NC}"
echo -e "${CYAN}AutoPackager Setup Script v1.0${NC}"
echo -e "${CYAN}==================================${NC}"
echo ""

# Step 1: Check Prerequisites
echo -e "${YELLOW}[1/8] Checking prerequisites...${NC}"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 not found!${NC}"
    echo "Please install Python 3.9+:"
    echo "  Ubuntu/Debian: sudo apt-get install python3 python3-pip python3-venv"
    echo "  MacOS: brew install python3"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo -e "  ${GREEN}Found: $PYTHON_VERSION${NC}"

# Check pip
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}WARNING: pip3 not found, attempting to install...${NC}"
    sudo apt-get install -y python3-pip || true
fi

# Check Git
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}WARNING: Git not found. You may need it for updates.${NC}"
else
    echo -e "  ${GREEN}Git: OK${NC}"
fi

echo ""

# Step 2: Install System Dependencies
echo -e "${YELLOW}[2/8] Installing system dependencies...${NC}"

# Detect OS
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    if command -v apt-get &> /dev/null; then
        echo "  Installing packages via apt-get..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq cabextract || echo -e "${YELLOW}  cabextract install failed (optional)${NC}"

        if [ "$SKIP_REDIS" = false ]; then
            sudo apt-get install -y -qq redis-server || echo -e "${YELLOW}  Redis install failed${NC}"
        fi

        if [ "$USE_SQLITE" = false ]; then
            sudo apt-get install -y -qq postgresql postgresql-contrib || echo -e "${YELLOW}  PostgreSQL install skipped${NC}"
        fi

        echo -e "  ${GREEN}System dependencies installed${NC}"
    elif command -v yum &> /dev/null; then
        echo "  Installing packages via yum..."
        sudo yum install -y cabextract

        if [ "$SKIP_REDIS" = false ]; then
            sudo yum install -y redis
        fi

        if [ "$USE_SQLITE" = false ]; then
            sudo yum install -y postgresql-server postgresql-contrib
        fi

        echo -e "  ${GREEN}System dependencies installed${NC}"
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    if command -v brew &> /dev/null; then
        echo "  Installing packages via Homebrew..."
        brew install cabextract

        if [ "$SKIP_REDIS" = false ]; then
            brew install redis
        fi

        if [ "$USE_SQLITE" = false ]; then
            brew install postgresql
        fi

        echo -e "  ${GREEN}System dependencies installed${NC}"
    else
        echo -e "${YELLOW}WARNING: Homebrew not found. Install from https://brew.sh${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: Unknown OS, skipping system package installation${NC}"
fi

echo ""

# Step 3: Create Virtual Environment
echo -e "${YELLOW}[3/8] Creating Python virtual environment...${NC}"

if [ -d "venv" ]; then
    echo -e "  ${YELLOW}Virtual environment already exists, skipping...${NC}"
else
    python3 -m venv venv
    echo -e "  ${GREEN}Virtual environment created successfully${NC}"
fi

echo ""

# Step 4: Install Python Dependencies
echo -e "${YELLOW}[4/8] Installing Python dependencies...${NC}"

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip --quiet

# Install requirements
pip install -r requirements.txt --quiet
if [ $? -eq 0 ]; then
    echo -e "  ${GREEN}Dependencies installed successfully${NC}"
else
    echo -e "${RED}ERROR: Failed to install dependencies${NC}"
    exit 1
fi

echo ""

# Step 5: Configure Redis
echo -e "${YELLOW}[5/8] Configuring Redis...${NC}"

if [ "$SKIP_REDIS" = false ]; then
    if command -v redis-server &> /dev/null; then
        echo -e "  ${GREEN}Redis is installed${NC}"

        # Check if Redis is running
        if systemctl is-active --quiet redis-server 2>/dev/null || systemctl is-active --quiet redis 2>/dev/null; then
            echo -e "  ${GREEN}Redis is already running${NC}"
        else
            echo -e "  ${YELLOW}Redis is not running. Start it with:${NC}"
            echo -e "    ${CYAN}sudo systemctl start redis-server${NC}"
            echo -e "    ${CYAN}or: redis-server${NC}"
        fi
    else
        echo -e "${YELLOW}WARNING: Redis not installed${NC}"
        echo "  Install with: sudo apt-get install redis-server"
    fi
else
    echo -e "  ${YELLOW}Redis installation skipped${NC}"
fi

echo ""

# Step 6: Create .env file
echo -e "${YELLOW}[6/8] Creating .env configuration file...${NC}"

if [ -f ".env" ]; then
    echo -e "  ${YELLOW}.env file already exists, skipping...${NC}"
else
    cp .env.template .env
    echo -e "  ${GREEN}.env file created from template${NC}"
    echo -e "  ${YELLOW}IMPORTANT: Edit .env file with your Azure credentials!${NC}"
fi

echo ""

# Step 7: Configure Database
echo -e "${YELLOW}[7/8] Configuring database...${NC}"

if [ "$USE_SQLITE" = true ]; then
    echo -e "  ${YELLOW}Configuring for SQLite (testing mode)...${NC}"

    # Backup original config
    cp autopackager/config/config.yaml autopackager/config/config.yaml.backup

    # Update config for SQLite (simple sed replacement)
    cat > autopackager/config/config.yaml.tmp << 'EOF'
# AutoPackager Configuration - SQLite Mode

database:
  type: "sqlite"
  path: "data/autopackager.db"

EOF
    # Append the rest of the config (skip database section)
    sed -n '/^redis:/,$p' autopackager/config/config.yaml >> autopackager/config/config.yaml.tmp
    mv autopackager/config/config.yaml.tmp autopackager/config/config.yaml

    echo -e "  ${GREEN}SQLite configuration set${NC}"
    echo -e "  ${YELLOW}Original config backed up to config.yaml.backup${NC}"
else
    echo -e "  ${GREEN}Using PostgreSQL configuration${NC}"

    if command -v psql &> /dev/null; then
        echo -e "  ${YELLOW}PostgreSQL detected. Create database with:${NC}"
        echo -e "    ${CYAN}sudo -u postgres psql${NC}"
        echo -e "    ${CYAN}CREATE DATABASE autopackager;${NC}"
        echo -e "    ${CYAN}CREATE USER autopackager_user WITH PASSWORD 'your_password';${NC}"
        echo -e "    ${CYAN}GRANT ALL PRIVILEGES ON DATABASE autopackager TO autopackager_user;${NC}"
    else
        echo -e "${YELLOW}WARNING: PostgreSQL not found${NC}"
        echo "  Consider using --sqlite flag for testing"
    fi
fi

echo ""

# Step 8: Initialize Database
echo -e "${YELLOW}[8/8] Initializing database...${NC}"

# Create data directories
mkdir -p data/downloads
mkdir -p data/packages
mkdir -p data/logs
mkdir -p data/catalogs/dell
mkdir -p data/catalogs/hp
mkdir -p data/catalogs/lenovo
mkdir -p tools

echo -e "  ${GREEN}Data directories created${NC}"

# Initialize database
python cli.py init

if [ $? -eq 0 ]; then
    echo -e "  ${GREEN}Database initialized successfully${NC}"
else
    echo -e "  ${YELLOW}WARNING: Database initialization had issues${NC}"
    echo -e "  ${YELLOW}You may need to configure your database settings in .env${NC}"
fi

echo ""
echo -e "${CYAN}==================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${CYAN}==================================${NC}"
echo ""

# Next Steps
echo -e "${YELLOW}NEXT STEPS:${NC}"
echo ""
echo -e "${WHITE}1. Edit .env file with your Azure credentials:${NC}"
echo "   - AZURE_TENANT_ID"
echo "   - AZURE_CLIENT_ID"
echo "   - AZURE_CLIENT_SECRET"
echo "   - RING0_GROUP_ID, RING1_GROUP_ID, RING2_GROUP_ID, RING3_GROUP_ID"
echo ""

echo -e "${WHITE}2. Download IntuneWinAppUtil.exe (if using Windows):${NC}"
echo "   https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool"
echo "   Place it in: tools/IntuneWinAppUtil.exe"
echo ""

echo -e "${WHITE}3. Start Redis (in a new terminal):${NC}"
echo -e "   ${CYAN}redis-server${NC}"
echo "   or:"
echo -e "   ${CYAN}redis-server redis.conf${NC}"
echo ""

echo -e "${WHITE}4. Start Celery Worker (in a new terminal):${NC}"
echo -e "   ${CYAN}source venv/bin/activate${NC}"
echo -e "   ${CYAN}python cli.py worker start${NC}"
echo ""

echo -e "${WHITE}5. Create your first driver job (in another terminal):${NC}"
echo -e "   ${CYAN}source venv/bin/activate${NC}"
echo -e "   ${CYAN}python cli.py create-driver-job --vendor dell --model \"Latitude 5420\"${NC}"
echo ""

echo -e "${WHITE}6. Monitor jobs:${NC}"
echo -e "   ${CYAN}python cli.py jobs list${NC}"
echo ""

echo -e "${YELLOW}For detailed instructions, see IMPLEMENTATION_GUIDE.md${NC}"
echo ""

# Create helper scripts
echo -e "${YELLOW}Creating helper scripts...${NC}"

# start-redis.sh
cat > start-redis.sh << 'EOF'
#!/bin/bash
echo "Starting Redis Server..."
redis-server redis.conf
EOF
chmod +x start-redis.sh

# start-worker.sh
cat > start-worker.sh << 'EOF'
#!/bin/bash
echo "Activating Python virtual environment..."
source venv/bin/activate
echo "Starting Celery Worker..."
python cli.py worker start
EOF
chmod +x start-worker.sh

# create-job.sh
cat > create-job.sh << 'EOF'
#!/bin/bash
source venv/bin/activate
python cli.py create-driver-job "$@"
EOF
chmod +x create-job.sh

# list-jobs.sh
cat > list-jobs.sh << 'EOF'
#!/bin/bash
source venv/bin/activate
python cli.py jobs list "$@"
EOF
chmod +x list-jobs.sh

echo -e "  ${GREEN}Created start-redis.sh${NC}"
echo -e "  ${GREEN}Created start-worker.sh${NC}"
echo -e "  ${GREEN}Created create-job.sh${NC}"
echo -e "  ${GREEN}Created list-jobs.sh${NC}"
echo ""

echo -e "${YELLOW}Quick start commands:${NC}"
echo -e "  ${WHITE}./start-redis.sh${NC}    - Start Redis server"
echo -e "  ${WHITE}./start-worker.sh${NC}   - Start Celery worker"
echo -e "  ${WHITE}./create-job.sh --vendor dell --model \"YourModel\"${NC}"
echo -e "  ${WHITE}./list-jobs.sh${NC}      - List all jobs"
echo ""

echo -e "${GREEN}Setup complete! You're ready to use AutoPackager.${NC}"
echo ""

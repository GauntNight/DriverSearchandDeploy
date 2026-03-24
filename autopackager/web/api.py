"""FastAPI Application for AutoPackager Web Dashboard"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# Load configuration
config = get_config()
dashboard_config = config.get('dashboard', {})

# Initialize FastAPI application
app = FastAPI(
    title="AutoPackager Dashboard API",
    description="REST API for AutoPackager deployment monitoring dashboard",
    version="1.0.0"
)

# Configure CORS
cors_origins = dashboard_config.get('cors_origins', [
    "http://localhost:8000",
    "http://127.0.0.1:8000"
])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    logger.info(f"Mounted static files from {static_dir}")
else:
    logger.warning(f"Static directory not found: {static_dir}")

logger.info("FastAPI application initialized")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "autopackager-dashboard"}

"""FastAPI Application for AutoPackager Web Dashboard"""

from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger
from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import JobState

logger = get_logger(__name__)

# Initialize orchestration engine
engine = OrchestrationEngine()

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


@app.get("/api/jobs")
async def list_jobs(
    state: Optional[str] = Query(None, description="Filter by job state"),
    limit: Optional[int] = Query(100, ge=1, le=1000, description="Maximum number of jobs to return")
):
    """
    List all jobs or filter by state

    Query Parameters:
    - state: Optional filter by JobState (pending, discovering, packaging, testing, deploying, completed, failed, cancelled)
    - limit: Maximum number of jobs to return (default: 100, max: 1000)
    """
    try:
        if state:
            # Validate state
            try:
                job_state = JobState(state.lower())
            except ValueError:
                valid_states = [s.value for s in JobState]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid state '{state}'. Valid states: {', '.join(valid_states)}"
                )

            jobs = engine.get_jobs_by_state(job_state, limit=limit)
        else:
            jobs = engine.get_all_jobs(limit=limit)

        # Convert jobs to dict format
        jobs_data = [job.to_dict() for job in jobs]

        return {
            "jobs": jobs_data,
            "count": len(jobs_data),
            "filter": {"state": state} if state else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing jobs", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    """
    Get a specific job by ID

    Path Parameters:
    - job_id: The job ID to retrieve
    """
    try:
        job = engine.get_job(job_id)

        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return job.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting job", job_id=job_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

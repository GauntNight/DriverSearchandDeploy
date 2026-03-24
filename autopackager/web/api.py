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
from autopackager.models.deployment import DeploymentStatus
from autopackager.services.dashboard_service import DashboardService

logger = get_logger(__name__)

# Initialize orchestration engine
engine = OrchestrationEngine()

# Initialize dashboard service
dashboard_service = DashboardService()

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


@app.get("/api/deployments")
async def list_deployments(
    status: Optional[str] = Query(None, description="Filter by deployment status"),
    limit: Optional[int] = Query(100, ge=1, le=1000, description="Maximum number of deployments to return")
):
    """
    List all deployments or filter by status

    Query Parameters:
    - status: Optional filter by DeploymentStatus (pending, in_progress, successful, failed, superseded)
    - limit: Maximum number of deployments to return (default: 100, max: 1000)
    """
    try:
        deployment_status = None
        if status:
            # Validate status
            try:
                deployment_status = DeploymentStatus(status.lower())
            except ValueError:
                valid_statuses = [s.value for s in DeploymentStatus]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status '{status}'. Valid statuses: {', '.join(valid_statuses)}"
                )

        deployments = dashboard_service.get_deployments(
            status=deployment_status,
            limit=limit
        )

        return {
            "deployments": deployments,
            "count": len(deployments),
            "filter": {"status": status} if status else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing deployments", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/api/deployments/rings")
async def get_deployment_rings():
    """
    Get deployment status grouped by ring

    Returns deployment information organized by deployment rings,
    including device counts and success/failure statistics for each ring.
    """
    try:
        ring_status = dashboard_service.get_deployment_ring_status()

        return ring_status

    except Exception as e:
        logger.error("Error getting deployment ring status", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/api/stats")
async def get_statistics():
    """
    Get overall dashboard statistics

    Returns comprehensive statistics including:
    - Job counts (total, by state, recent 24h)
    - Deployment counts (total, successful, failed, in progress, recent 24h)
    - Package counts (total, tested, deployed)
    - Timestamp of when statistics were generated
    """
    try:
        stats = dashboard_service.get_statistics()

        return stats

    except Exception as e:
        logger.error("Error getting statistics", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/api/activity")
async def get_recent_activity(
    limit: Optional[int] = Query(50, ge=1, le=500, description="Maximum number of activity items to return")
):
    """
    Get recent activity timeline

    Returns a chronologically ordered timeline of recent jobs and deployments,
    providing a unified view of system activity.

    Query Parameters:
    - limit: Maximum number of activity items to return (default: 50, max: 500)
    """
    try:
        activity = dashboard_service.get_recent_activity(limit=limit)

        return {
            "activity": activity,
            "count": len(activity)
        }

    except Exception as e:
        logger.error("Error getting recent activity", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

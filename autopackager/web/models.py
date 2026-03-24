"""Pydantic Models for API Responses"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    """Job API response model"""
    id: int
    job_type: Optional[str] = None
    state: Optional[str] = None
    software_title: str
    current_version: Optional[str] = None
    target_version: Optional[str] = None
    vendor: Optional[str] = None
    hardware_model: Optional[str] = None
    driver_type: Optional[str] = None
    download_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: Optional[int] = 0
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class DeploymentResponse(BaseModel):
    """Deployment API response model"""
    id: int
    package_id: int
    intune_app_id: str
    ring_id: str
    ring_name: Optional[str] = None
    status: Optional[str] = None
    target_device_count: int = 0
    successful_installs: int = 0
    failed_installs: int = 0
    pending_installs: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deployed_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class PackageResponse(BaseModel):
    """Package API response model"""
    id: int
    name: str
    version: str
    vendor: Optional[str] = None
    intunewin_path: str
    install_command: Optional[str] = None
    uninstall_command: Optional[str] = None
    detection_rules: Optional[List[Any]] = Field(default_factory=list)
    tested: bool = False
    test_passed: Optional[bool] = None
    vm_test_results: Optional[Dict[str, Any]] = Field(default_factory=dict)
    intune_app_id: Optional[str] = None
    deployed: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class JobStatsResponse(BaseModel):
    """Job statistics response model"""
    total: int
    by_state: Dict[str, int]
    recent_24h: int


class DeploymentStatsResponse(BaseModel):
    """Deployment statistics response model"""
    total: int
    successful: int
    failed: int
    in_progress: int
    recent_24h: int


class PackageStatsResponse(BaseModel):
    """Package statistics response model"""
    total: int
    tested: int
    deployed: int


class DashboardStatsResponse(BaseModel):
    """Overall dashboard statistics response model"""
    jobs: JobStatsResponse
    deployments: DeploymentStatsResponse
    packages: PackageStatsResponse
    last_updated: str


class ActivityEventResponse(BaseModel):
    """Activity timeline event response model"""
    timestamp: str
    event_type: str
    title: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class RingStatusResponse(BaseModel):
    """Deployment ring status response model"""
    ring_id: str
    ring_name: str
    total_deployments: int
    successful: int
    failed: int
    pending: int
    in_progress: int
    success_rate: float = 0.0

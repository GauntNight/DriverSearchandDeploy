"""Job Model for Orchestration Engine"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, JSON, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class JobState(str, Enum):
    """Job state enumeration"""
    PENDING = "pending"
    DISCOVERING = "discovering"
    PACKAGING = "packaging"
    TESTING = "testing"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    """Job type enumeration"""
    DRIVER_UPDATE = "driver_update"
    SOFTWARE_UPDATE = "software_update"
    NEW_SOFTWARE = "new_software"


class Job(Base):
    """Job tracking model"""
    __tablename__ = 'jobs'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Job metadata
    job_type = Column(SQLEnum(JobType), nullable=False)
    state = Column(SQLEnum(JobState), nullable=False, default=JobState.PENDING)

    # Software/Driver information
    software_title = Column(String(255), nullable=False)
    current_version = Column(String(100))
    target_version = Column(String(100))
    vendor = Column(String(100))  # Dell, HP, Lenovo, Adobe, etc.

    # OEM-specific (for drivers)
    hardware_model = Column(String(255))
    driver_type = Column(String(100))  # chipset, network, graphics, etc.

    # URLs and metadata
    download_url = Column(String(1024))
    release_notes = Column(String(4096))

    # Package information
    package_id = Column(Integer)
    intunewin_path = Column(String(512))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)

    # Status tracking
    retry_count = Column(Integer, default=0)
    error_message = Column(String(2048))

    # Metadata (flexible JSON field for agent-specific data)
    metadata = Column(JSON, default={})

    def __repr__(self):
        return f"<Job {self.id}: {self.software_title} ({self.state})>"

    def to_dict(self):
        """Convert job to dictionary"""
        return {
            'id': self.id,
            'job_type': self.job_type.value if self.job_type else None,
            'state': self.state.value if self.state else None,
            'software_title': self.software_title,
            'current_version': self.current_version,
            'target_version': self.target_version,
            'vendor': self.vendor,
            'hardware_model': self.hardware_model,
            'driver_type': self.driver_type,
            'download_url': self.download_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'retry_count': self.retry_count,
            'error_message': self.error_message,
            'metadata': self.metadata
        }

"""Deployment Model"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, JSON, Enum as SQLEnum, ForeignKey
from .base import Base



class DeploymentStatus(str, Enum):
    """Deployment status enumeration"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


class Deployment(Base):
    """Deployment tracking model"""
    __tablename__ = 'deployments'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Package reference
    package_id = Column(Integer, ForeignKey('packages.id'), nullable=False)

    # Intune information
    intune_app_id = Column(String(255), nullable=False)
    intune_assignment_id = Column(String(255))

    # Deployment ring
    ring_id = Column(String(50), nullable=False)
    ring_name = Column(String(100))
    entra_group_id = Column(String(255))

    # Status
    status = Column(SQLEnum(DeploymentStatus), nullable=False, default=DeploymentStatus.PENDING)

    # Statistics
    target_device_count = Column(Integer, default=0)
    successful_installs = Column(Integer, default=0)
    failed_installs = Column(Integer, default=0)
    pending_installs = Column(Integer, default=0)
    not_applicable_installs = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deployed_at = Column(DateTime)
    completed_at = Column(DateTime)
    last_status_check = Column(DateTime)

    # Rollback tracking
    rolled_back_at = Column(DateTime)
    rollback_reason = Column(String(1024))
    previous_package_id = Column(Integer, ForeignKey('packages.id'))

    # Promotion tracking
    promoted_at = Column(DateTime)
    promotion_eligibility_time = Column(DateTime)
    promotion_blocked_reason = Column(String(1024))

    # Error tracking
    error_message = Column(String(2048))

    # Metadata
    deployment_metadata = Column('metadata', JSON, default={})

    # Device Status Details
    device_status_details = Column(JSON, default=list)

    def __repr__(self):
        return f"<Deployment {self.id}: Ring {self.ring_id} ({self.status})>"

    def to_dict(self):
        """Convert deployment to dictionary"""
        return {
            'id': self.id,
            'package_id': self.package_id,
            'intune_app_id': self.intune_app_id,
            'ring_id': self.ring_id,
            'ring_name': self.ring_name,
            'status': self.status.value if self.status else None,
            'target_device_count': self.target_device_count,
            'successful_installs': self.successful_installs,
            'failed_installs': self.failed_installs,
            'pending_installs': self.pending_installs,
            'not_applicable_installs': self.not_applicable_installs,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'deployed_at': self.deployed_at.isoformat() if self.deployed_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'last_status_check': self.last_status_check.isoformat() if self.last_status_check else None,
            'rolled_back_at': self.rolled_back_at.isoformat() if self.rolled_back_at else None,
            'rollback_reason': self.rollback_reason,
            'previous_package_id': self.previous_package_id,
            'promoted_at': self.promoted_at.isoformat() if self.promoted_at else None,
            'promotion_eligibility_time': self.promotion_eligibility_time.isoformat() if self.promotion_eligibility_time else None,
            'promotion_blocked_reason': self.promotion_blocked_reason,
            'error_message': self.error_message,
            'metadata': self.deployment_metadata,
            'device_status_details': self.device_status_details
        }

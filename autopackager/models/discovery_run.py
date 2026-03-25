"""DiscoveryRun Model for Catalog Discovery Tracking"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON
from .base import Base


class DiscoveryRun(Base):
    """Discovery run tracking model"""
    __tablename__ = 'discovery_runs'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)

    # Metrics
    catalogs_scanned = Column(Integer, default=0, nullable=False)
    new_versions_found = Column(Integer, default=0, nullable=False)
    jobs_created = Column(Integer, default=0, nullable=False)

    # OEM-specific results (JSON for flexibility)
    oem_results = Column(JSON, default=dict)

    # Error tracking
    error_message = Column(String(2048))

    def __repr__(self):
        return f"<DiscoveryRun {self.id}: {self.catalogs_scanned} catalogs, {self.new_versions_found} versions, {self.jobs_created} jobs>"

    def to_dict(self):
        """Convert discovery run to dictionary"""
        return {
            'id': self.id,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'catalogs_scanned': self.catalogs_scanned,
            'new_versions_found': self.new_versions_found,
            'jobs_created': self.jobs_created,
            'oem_results': self.oem_results,
            'error_message': self.error_message
        }

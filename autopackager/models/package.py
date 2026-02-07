"""Package Model"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, Boolean
from .base import Base



class Package(Base):
    """Package tracking model"""
    __tablename__ = 'packages'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Package identification
    name = Column(String(255), nullable=False)
    version = Column(String(100), nullable=False)
    vendor = Column(String(100))

    # Package files
    intunewin_path = Column(String(512), nullable=False)
    installer_path = Column(String(512))
    psadt_script_path = Column(String(512))

    # Installation commands
    install_command = Column(String(1024))
    uninstall_command = Column(String(1024))

    # Detection rules (stored as JSON)
    detection_rules = Column(JSON, default=[])

    # Requirements
    requirements = Column(JSON, default={})

    # Testing results
    tested = Column(Boolean, default=False)
    test_passed = Column(Boolean)
    test_logs = Column(String(4096))

    # Intune deployment
    intune_app_id = Column(String(255))
    deployed = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Metadata
    package_metadata = Column('metadata', JSON, default={})

    def __repr__(self):
        return f"<Package {self.id}: {self.name} v{self.version}>"

    def to_dict(self):
        """Convert package to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'vendor': self.vendor,
            'intunewin_path': self.intunewin_path,
            'install_command': self.install_command,
            'uninstall_command': self.uninstall_command,
            'detection_rules': self.detection_rules,
            'tested': self.tested,
            'test_passed': self.test_passed,
            'intune_app_id': self.intune_app_id,
            'deployed': self.deployed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'metadata': self.package_metadata
        }

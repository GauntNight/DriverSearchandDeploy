"""Database Connection and Session Management"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from contextlib import contextmanager

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# Global engine and session factory
_engine = None
_session_factory = None


def get_database_url():
    """Construct database URL from config"""
    config = get_config()
    db_config = config['database']

    if db_config['type'] == 'postgresql':
        return (
            f"postgresql://{db_config['user']}:{db_config['password']}"
            f"@{db_config['host']}:{db_config['port']}/{db_config['name']}"
        )
    elif db_config['type'] == 'sqlite':
        return f"sqlite:///{db_config.get('path', 'autopackager.db')}"
    else:
        raise ValueError(f"Unsupported database type: {db_config['type']}")


def init_db(create_tables=True):
    """Initialize database connection and create tables if needed"""
    global _engine, _session_factory

    database_url = get_database_url()
    logger.info("Initializing database", url=database_url.split('@')[-1])  # Don't log password

    _engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

    _session_factory = scoped_session(
        sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    )

    if create_tables:
        from autopackager.models.job import Base as JobBase
        from autopackager.models.package import Base as PackageBase
        from autopackager.models.deployment import Base as DeploymentBase

        logger.info("Creating database tables")
        JobBase.metadata.create_all(_engine)
        PackageBase.metadata.create_all(_engine)
        DeploymentBase.metadata.create_all(_engine)

    return _engine


def get_db_session():
    """Get a database session"""
    if _session_factory is None:
        init_db()
    return _session_factory()


@contextmanager
def db_session_scope():
    """Provide a transactional scope for database operations"""
    session = get_db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

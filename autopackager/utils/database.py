"""Database Connection and Session Management"""

import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from contextlib import contextmanager

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# Global engine and session factory
_engine = None
_session_factory = None

# Per-thread re-entrancy tracking for db_session_scope. The session factory is a
# scoped_session keyed by thread, so two nested ``with db_session_scope()`` calls
# on the same thread share one underlying Session. Without depth tracking, the
# inner scope's ``session.close()`` detaches every ORM object the outer scope is
# still holding -- callers iterating over a query and calling helper methods
# that also open a scope hit DetachedInstanceError on the second iteration.
_scope_state = threading.local()


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
        from autopackager.models.discovery_run import Base as DiscoveryRunBase

        logger.info("Creating database tables")
        JobBase.metadata.create_all(_engine)
        PackageBase.metadata.create_all(_engine)
        DeploymentBase.metadata.create_all(_engine)
        DiscoveryRunBase.metadata.create_all(_engine)

    return _engine


def get_db_session():
    """Get a database session"""
    if _session_factory is None:
        init_db()
    return _session_factory()


@contextmanager
def db_session_scope():
    """Provide a transactional scope for database operations.

    Re-entrant on a single thread: nested ``with db_session_scope()`` calls
    share the outermost scope's Session and only the outermost scope commits /
    rolls back / closes. This lets helper methods open their own scope without
    detaching ORM objects the caller is iterating over (see the original
    ``check_all_deployments`` DetachedInstanceError when an inner scope closed
    the shared scoped Session mid-loop).
    """
    depth = getattr(_scope_state, 'depth', 0)
    _scope_state.depth = depth + 1
    is_outermost = (depth == 0)
    session = get_db_session()
    try:
        yield session
        if is_outermost:
            session.commit()
    except Exception:
        if is_outermost:
            session.rollback()
        raise
    finally:
        _scope_state.depth = depth
        if is_outermost:
            session.close()
            if _session_factory is not None:
                _session_factory.remove()

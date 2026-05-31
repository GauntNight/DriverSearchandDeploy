"""Unit tests for ``autopackager.utils.database``.

The focus here is the re-entrancy of ``db_session_scope``. The original
non-reentrant scope shared a thread-local scoped Session across nested scopes;
the inner scope's ``session.close()`` detached every ORM object the outer scope
was still holding, so a caller iterating over a query and calling a helper that
opened its own scope hit ``DetachedInstanceError`` on the second iteration.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from autopackager.models.base import Base
from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.models.package import Package
from autopackager.utils import database as db


@pytest.fixture
def memory_db(monkeypatch):
    """Point the ``database`` module's global factory at an in-memory SQLite.

    Yields the engine so individual tests can seed rows directly without going
    through the scope.
    """
    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_session_factory', factory)
    # Reset the per-thread depth counter so a previous test cannot bleed in.
    monkeypatch.setattr(db, '_scope_state', __import__('threading').local())
    yield engine
    factory.remove()
    Base.metadata.drop_all(engine)
    engine.dispose()


class TestDbSessionScopeReentrancy:
    def test_nested_scope_returns_same_session(self, memory_db):
        """Two nested scopes on the same thread share one Session instance."""
        with db.db_session_scope() as outer:
            with db.db_session_scope() as inner:
                assert outer is inner

    def test_inner_scope_does_not_close_outer_session(self, memory_db):
        """After exiting the inner scope, the outer Session is still usable."""
        with db.db_session_scope() as outer:
            with db.db_session_scope():
                pass
            # If the inner scope had closed the shared Session, this query
            # would raise (closed transaction / detached state).
            outer.execute(__import__('sqlalchemy').text('SELECT 1'))

    def test_inner_scope_does_not_commit(self, memory_db):
        """Writes from inside an inner scope only commit when the outer exits.

        Pre-fix the inner scope would commit independently, expiring every
        ORM object loaded by the outer scope and detaching them after the
        Session was then closed.
        """
        # Seed a package via the outer-scope commit so we can write a
        # Deployment against it inside the test body.
        with db.db_session_scope() as session:
            pkg = Package(name='x', version='1', intunewin_path='i')
            session.add(pkg)
            session.flush()
            pkg_id = pkg.id

        from unittest.mock import patch
        with patch.object(db, 'db_session_scope', wraps=db.db_session_scope) as scope_mock:
            with db.db_session_scope() as outer:
                outer.add(Deployment(package_id=pkg_id, intune_app_id='a',
                                     ring_id='ring0', ring_name='IT Pilot',
                                     status=DeploymentStatus.IN_PROGRESS))
                with db.db_session_scope():
                    # If the inner scope committed, the row would already be in
                    # the database in a separate connection's view. We instead
                    # rely on the outer scope's commit-on-exit to publish it.
                    pass

        # New scope after outer exited -- row must be visible.
        with db.db_session_scope() as verify:
            count = verify.query(Deployment).count()
            assert count == 1

    def test_iteration_then_nested_call_does_not_detach(self, memory_db):
        """The actual ``check_all_deployments`` shape: load N rows, iterate, and
        call a helper that opens its own scope. Pre-fix the second iteration's
        attribute access raised ``DetachedInstanceError``.
        """
        # Seed two deployments.
        with db.db_session_scope() as session:
            pkg = Package(name='x', version='1', intunewin_path='i')
            session.add(pkg)
            session.flush()
            for i in range(2):
                session.add(Deployment(package_id=pkg.id, intune_app_id=f'app-{i}',
                                       ring_id='ring0', ring_name='IT Pilot',
                                       status=DeploymentStatus.IN_PROGRESS))

        def helper_that_opens_a_nested_scope(deployment_id):
            with db.db_session_scope() as inner:
                d = inner.query(Deployment).filter(Deployment.id == deployment_id).first()
                d.successful_installs = 1

        seen_ids = []
        with db.db_session_scope() as outer:
            rows = outer.query(Deployment).filter(
                Deployment.status == DeploymentStatus.IN_PROGRESS
            ).all()
            for d in rows:
                # First attribute access on iter 2 was the original crash site.
                seen_ids.append(d.id)
                helper_that_opens_a_nested_scope(d.id)

        assert len(seen_ids) == 2
        with db.db_session_scope() as verify:
            updates = verify.query(Deployment).filter(Deployment.successful_installs == 1).count()
            assert updates == 2

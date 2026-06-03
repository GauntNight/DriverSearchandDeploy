"""AutoPackager demo console.

A removable, demo-grade three-panel "mission control" UI layered on top of the
existing FastAPI app + agents. Nothing in here is destined for customer hands
except the deterministic catalog path; the Claude research bridge is
operator-only (see demo/README.md).

The whole package is additive: deleting the `demo/` directory and the three
additive lines in `autopackager/web/api.py` removes it cleanly. The core
pipeline imports `demo.events` only through a lazy, exception-swallowing hook
(see `autopackager/orchestration/tasks.py`), so the core keeps working with the
package absent.
"""

__all__ = ["events", "intake", "preflight", "claude_bridge"]

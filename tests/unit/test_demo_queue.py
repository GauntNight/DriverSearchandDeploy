"""Unit tests for the packaging queue (delta -> packaging jobs).

Covers acquisition resolution, queue-row creation, the per-item acquire+package
flow (hit / miss / escalate / awaiting-installer), the sequential batch runner
with cancel semantics, and cancel itself. External services are never touched:
the OrchestrationEngine, intake enqueue path, research bridge, Redis events, and
catalog are all mocked at the module boundary.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from demo import queue as pkg_queue
from demo.intake import Analysis
from autopackager.models.job import JobState, JobType


def _analysis(**kw):
    """Build a minimal Analysis for the given overrides."""
    base = dict(kind="msi", path="C:/sb/app.msi", filename="app.msi", branch="hit")
    base.update(kw)
    return Analysis(**base)


def _job(state):
    j = Mock()
    j.state = state
    j.job_metadata = {}
    return j


# --- resolve_acquisition ---------------------------------------------------

class TestResolveAcquisition(unittest.TestCase):
    def test_catalog_url_fast_path(self):
        entry = Mock()
        entry.canonical_download_url = "https://vendor.example/app.msi"
        cat = Mock()
        cat.by_id.return_value = entry
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=cat), \
             patch("demo.queue.claude_bridge.check_version") as bridge:
            out = pkg_queue.resolve_acquisition(
                {"name": "App", "in_catalog": "app-entry"})
        self.assertEqual(out["download_url"], "https://vendor.example/app.msi")
        self.assertEqual(out["source"], "catalog")
        bridge.assert_not_called()  # deterministic — never asked the model

    def test_falls_back_to_research_bridge(self):
        entry = Mock()
        entry.canonical_download_url = None
        cat = Mock()
        cat.by_id.return_value = entry
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=cat), \
             patch("demo.queue.claude_bridge.check_version",
                   return_value={"download_url": "https://x.example/tool.exe",
                                 "latest_version": "2.0"}) as bridge:
            out = pkg_queue.resolve_acquisition(
                {"name": "Tool", "publisher": "Vendor", "in_catalog": "tool"})
        self.assertEqual(out["download_url"], "https://x.example/tool.exe")
        self.assertEqual(out["source"], "research")
        bridge.assert_called_once()

    def test_no_url_returns_none(self):
        with patch("demo.queue.claude_bridge.check_version", return_value={}):
            out = pkg_queue.resolve_acquisition({"name": "Obscure", "publisher": "Nobody"})
        self.assertIsNone(out["download_url"])

    def test_non_installer_url_rejected(self):
        with patch("demo.queue.claude_bridge.check_version",
                   return_value={"download_url": "https://x.example/release-notes.html"}):
            out = pkg_queue.resolve_acquisition({"name": "Thing"})
        self.assertIsNone(out["download_url"])  # .html is not a known installer


# --- create_queue_job_row --------------------------------------------------

class TestCreateQueueJobRow(unittest.TestCase):
    def test_stamps_queue_origin_and_gates(self):
        with patch("demo.queue.intake.create_job_row", return_value=42) as crj:
            jid = pkg_queue.create_queue_job_row(
                {"name": "7-Zip", "publisher": "Igor Pavlov", "version": "26.01",
                 "bucket": "known_packageable", "in_catalog": "7-zip", "device_count": 5},
                batch_id="batch123")
        self.assertEqual(jid, 42)
        kwargs = crj.call_args.kwargs
        self.assertEqual(kwargs["job_type"], JobType.NEW_SOFTWARE)
        self.assertEqual(kwargs["software_title"], "7-Zip")
        self.assertTrue(kwargs["gate"])  # queue items are ALWAYS gated
        origin = kwargs["job_metadata"][pkg_queue.QUEUE_ORIGIN_KEY]
        self.assertEqual(origin["batch_id"], "batch123")
        self.assertEqual(origin["in_catalog"], "7-zip")
        self.assertEqual(origin["bucket"], "known_packageable")
        self.assertEqual(origin["state"], "queued")


# --- cancel ----------------------------------------------------------------

class TestCancel(unittest.TestCase):
    def test_cancel_marks_non_terminal_and_ends(self):
        engine = MagicMock()
        engine.get_job.return_value = _job(JobState.TESTING)
        with patch("autopackager.orchestration.engine.OrchestrationEngine", return_value=engine), \
             patch("demo.queue.events") as events:
            ok = pkg_queue.cancel_job(7)
        self.assertTrue(ok)
        engine.update_job_state.assert_called_once()
        self.assertEqual(engine.update_job_state.call_args.args[1], JobState.CANCELLED)
        events.publish_end.assert_called_once()

    def test_cancel_skips_already_terminal(self):
        engine = MagicMock()
        engine.get_job.return_value = _job(JobState.COMPLETED)
        with patch("autopackager.orchestration.engine.OrchestrationEngine", return_value=engine), \
             patch("demo.queue.events"):
            ok = pkg_queue.cancel_job(7)
        self.assertFalse(ok)
        engine.update_job_state.assert_not_called()

    def test_cancel_batch_counts(self):
        with patch("demo.queue.cancel_job", side_effect=[True, False, True]) as cj:
            n = pkg_queue.cancel_batch([1, 2, 3])
        self.assertEqual(n, 2)
        self.assertEqual(cj.call_count, 3)


# --- acquire_and_package ---------------------------------------------------

class TestAcquireAndPackage(unittest.TestCase):
    def setUp(self):
        # Silence the side-effecting helpers + events for every test here.
        self._p = [
            patch("demo.queue.events", MagicMock()),
            patch("demo.queue._is_cancelled", return_value=False),
            patch("demo.queue._set_origin_state"),
        ]
        for p in self._p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p])

    def test_known_hit_dispatches_gated_without_research(self):
        with patch("demo.queue.resolve_acquisition",
                   return_value={"download_url": "https://x/app.msi", "source": "catalog"}), \
             patch("demo.queue.intake.download_to_sandbox", return_value="C:/sb/app.msi"), \
             patch("demo.queue.intake.analyze", return_value=_analysis(branch="hit")), \
             patch("demo.queue.intake.update_software_metadata") as upd, \
             patch("demo.queue.intake.dispatch_pipeline") as disp, \
             patch("demo.queue.claude_bridge.research_and_learn") as research:
            out = pkg_queue.acquire_and_package(1, {"name": "App", "in_catalog": "app"})
        self.assertEqual(out, "dispatched")
        research.assert_not_called()           # hit needs no research
        upd.assert_called_once()
        disp.assert_called_once_with(1, gate=True)  # gated, always

    def test_candidate_miss_researches_then_dispatches(self):
        miss = _analysis(branch="miss")
        hit = _analysis(branch="hit")
        with patch("demo.queue.resolve_acquisition",
                   return_value={"download_url": "https://x/tool.exe", "source": "research"}), \
             patch("demo.queue.intake.download_to_sandbox", return_value="C:/sb/tool.exe"), \
             patch("demo.queue.intake.analyze", side_effect=[miss, hit]), \
             patch("demo.queue.intake.update_software_metadata"), \
             patch("demo.queue.intake.dispatch_pipeline") as disp, \
             patch("demo.queue.claude_bridge.research_and_learn") as research:
            out = pkg_queue.acquire_and_package(2, {"name": "Tool"})
        self.assertEqual(out, "dispatched")
        research.assert_called_once()
        disp.assert_called_once_with(2, gate=True)

    def test_no_source_parks_awaiting_installer(self):
        with patch("demo.queue.resolve_acquisition",
                   return_value={"download_url": None, "source": "research"}), \
             patch("demo.queue.intake.dispatch_pipeline") as disp, \
             patch("demo.queue.intake.download_to_sandbox") as dl:
            out = pkg_queue.acquire_and_package(3, {"name": "Obscure"})
        self.assertEqual(out, "awaiting_installer")
        dl.assert_not_called()    # nothing to fetch
        disp.assert_not_called()  # nothing dispatched

    def test_escalate_installer_fails_without_dispatch(self):
        engine = MagicMock()
        with patch("demo.queue.resolve_acquisition",
                   return_value={"download_url": "https://x/real.exe", "source": "research"}), \
             patch("demo.queue.intake.download_to_sandbox", return_value="C:/sb/real.exe"), \
             patch("demo.queue.intake.analyze",
                   return_value=_analysis(kind="exe", filename="real.exe",
                                          escalate=True, escalate_reason="bundleware")), \
             patch("demo.queue.intake.dispatch_pipeline") as disp, \
             patch("autopackager.orchestration.engine.OrchestrationEngine", return_value=engine):
            out = pkg_queue.acquire_and_package(4, {"name": "RealPlayer"})
        self.assertEqual(out, "failed")
        disp.assert_not_called()
        self.assertEqual(engine.update_job_state.call_args.args[1], JobState.FAILED)


# --- run_batch -------------------------------------------------------------

class TestRunBatch(unittest.TestCase):
    def _specs(self, *ids):
        return [{"job_id": i, "candidate": {"name": f"app{i}"}} for i in ids]

    def test_sequential_waits_each_item(self):
        with patch("demo.queue._is_cancelled", return_value=False), \
             patch("demo.queue.acquire_and_package", return_value="dispatched") as acq, \
             patch("demo.queue._wait_for_settle", return_value="gate") as wait:
            pkg_queue.run_batch(self._specs(1, 2))
        self.assertEqual(acq.call_count, 2)
        self.assertEqual(wait.call_count, 2)  # waited for each dispatched item

    def test_cancel_during_item_stops_remaining(self):
        with patch("demo.queue._is_cancelled", return_value=False), \
             patch("demo.queue.acquire_and_package", return_value="dispatched"), \
             patch("demo.queue._wait_for_settle", return_value="cancelled"), \
             patch("demo.queue.cancel_job") as cancel:
            pkg_queue.run_batch(self._specs(1, 2, 3))
        # item 1 cancelled mid-flight → remaining (2, 3) cancelled, batch stops.
        cancelled_ids = sorted(c.args[0] for c in cancel.call_args_list)
        self.assertEqual(cancelled_ids, [2, 3])

    def test_pre_cancelled_item_skipped(self):
        # First item already cancelled (skip), second proceeds.
        with patch("demo.queue._is_cancelled", side_effect=[True, False]), \
             patch("demo.queue.events", MagicMock()), \
             patch("demo.queue.acquire_and_package", return_value="dispatched") as acq, \
             patch("demo.queue._wait_for_settle", return_value="gate"):
            pkg_queue.run_batch(self._specs(1, 2))
        self.assertEqual(acq.call_count, 1)  # only the non-cancelled item ran

    def test_awaiting_item_does_not_block_batch(self):
        with patch("demo.queue._is_cancelled", return_value=False), \
             patch("demo.queue.acquire_and_package", return_value="awaiting_installer"), \
             patch("demo.queue._wait_for_settle") as wait:
            pkg_queue.run_batch(self._specs(1))
        wait.assert_not_called()  # a parked item is not waited on


# --- router helper ----------------------------------------------------------

class TestRouterCreateRows(unittest.TestCase):
    def test_filters_invalid_and_builds_specs(self):
        from demo import router

        with patch("demo.router.pkg_queue.create_queue_job_row",
                   side_effect=[101, 102]) as crj:
            batch_id, specs = router._create_queue_rows([
                {"name": "Good One", "publisher": "X"},
                {"name": "   "},          # blank name → skipped
                "not-a-dict",             # junk → skipped
                {"publisher": "no name"}, # no name → skipped
                {"name": "Good Two", "in_catalog": "g2"},
            ])
        self.assertEqual(len(specs), 2)
        self.assertEqual([s["job_id"] for s in specs], [101, 102])
        self.assertTrue(batch_id)
        self.assertEqual(crj.call_count, 2)


# --- CLI: queue-unmanaged ---------------------------------------------------

class TestCliQueueUnmanaged(unittest.TestCase):
    def test_queues_candidates_and_runs_batch(self):
        from click.testing import CliRunner
        from cli import cli

        delta = {
            "candidates": [{"name": "Azure CLI", "publisher": "Microsoft",
                            "version": "2.60", "bucket": "unmanaged_candidate",
                            "in_catalog": None, "device_count": 3}],
            "known_packageable": [{"name": "7-Zip", "publisher": "Igor Pavlov",
                                   "version": "26.01", "bucket": "known_packageable",
                                   "in_catalog": "7-zip", "device_count": 5}],
        }
        with patch("autopackager.services.software_delta.build_delta", return_value=delta), \
             patch("demo.queue.create_queue_job_row", side_effect=[201, 202]) as crj, \
             patch("demo.queue.run_batch") as run_batch:
            result = CliRunner().invoke(
                cli, ["queue-unmanaged", "--source", "local",
                      "--include-known", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(crj.call_count, 2)            # both buckets queued
        run_batch.assert_called_once()
        specs = run_batch.call_args.args[0]
        self.assertEqual([s["job_id"] for s in specs], [201, 202])

    def test_candidates_only_skips_known(self):
        from click.testing import CliRunner
        from cli import cli

        delta = {
            "candidates": [{"name": "Azure CLI", "bucket": "unmanaged_candidate"}],
            "known_packageable": [{"name": "7-Zip", "bucket": "known_packageable",
                                   "in_catalog": "7-zip"}],
        }
        with patch("autopackager.services.software_delta.build_delta", return_value=delta), \
             patch("demo.queue.create_queue_job_row", side_effect=[301]) as crj, \
             patch("demo.queue.run_batch"):
            result = CliRunner().invoke(
                cli, ["queue-unmanaged", "--source", "local", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(crj.call_count, 1)  # only the unmanaged candidate


if __name__ == "__main__":
    unittest.main()

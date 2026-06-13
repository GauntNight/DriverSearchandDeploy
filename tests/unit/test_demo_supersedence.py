"""Unit tests for the demo 'automatic supersedence' capability.

Covers the genuinely-new delta added on top of the already-live supersedence
engine: the version-check brain, the assignment auto-update plumbing, the
catalog↔Intune row enrichment, the upgrade-job metadata builders, and the
deployment-agent demo-scope branch. External services are not touched — the
version-check bridge runs in ``replay``/``off`` and Graph is mocked.
"""

import unittest
from unittest.mock import Mock, patch

from autopackager.utils.graph_client import GraphAPIClient
from autopackager.utils.installer_catalog import Catalog, CatalogEntry


# --- Graph assignment settings ---------------------------------------------

class TestAssignmentSettings(unittest.TestCase):
    def test_win32_auto_update_settings_enabled(self):
        s = GraphAPIClient.win32_auto_update_settings(True)
        self.assertEqual(s["@odata.type"], "#microsoft.graph.win32LobAppAssignmentSettings")
        self.assertEqual(s["autoUpdateSettings"]["autoUpdateSupersededAppsState"], "enabled")

    def test_win32_auto_update_settings_disabled(self):
        s = GraphAPIClient.win32_auto_update_settings(False)
        self.assertEqual(s["autoUpdateSettings"]["autoUpdateSupersededAppsState"], "notConfigured")

    def test_assign_includes_settings_when_provided(self):
        client = GraphAPIClient.__new__(GraphAPIClient)  # skip __init__/auth
        client.post = Mock(return_value={})
        settings = GraphAPIClient.win32_auto_update_settings(True)
        client.assign_app_to_group("app1", "grp1", settings=settings)
        payload = client.post.call_args[0][1]
        assignment = payload["mobileAppAssignments"][0]
        self.assertEqual(assignment["settings"], settings)
        self.assertEqual(assignment["target"]["groupId"], "grp1")

    def test_assign_omits_settings_by_default(self):
        client = GraphAPIClient.__new__(GraphAPIClient)
        client.post = Mock(return_value={})
        client.assign_app_to_group("app1", "grp1")
        payload = client.post.call_args[0][1]
        self.assertNotIn("settings", payload["mobileAppAssignments"][0])


# --- Version-check brain ----------------------------------------------------

class TestCheckVersion(unittest.TestCase):
    def test_off_mode_returns_not_newer(self):
        from demo import claude_bridge

        out = claude_bridge.check_version("7-zip", "23.01", None, mode="off")
        self.assertFalse(out["is_newer"])
        self.assertEqual(out["mode"], "off")

    def test_replay_detects_newer(self):
        from demo import claude_bridge

        with patch("demo.claude_bridge.time.sleep"):  # no real delay
            out = claude_bridge.check_version(
                "7-Zip", "23.01", None, mode="replay", slug="7-zip",
            )
        self.assertTrue(out["is_newer"])
        self.assertEqual(out["latest_version"], "26.01")

    def test_decide_is_newer_prefers_version_compare(self):
        from demo import claude_bridge

        # Model wrongly says not-newer, but 24.08 > 23.01 → trust the compare.
        self.assertTrue(claude_bridge._decide_is_newer("24.08", "23.01", False))
        # Equal versions are not newer even if the model claims so.
        self.assertFalse(claude_bridge._decide_is_newer("23.01", "23.01", True))
        # Unparseable current → fall back to the model's claim.
        self.assertTrue(claude_bridge._decide_is_newer("24.08", None, True))

    def test_bump_version_synthesizes_believable_next(self):
        from demo import claude_bridge as cb

        # Trailing .0 components are stripped so the bump lands meaningfully.
        self.assertEqual(cb._bump_version("3.0.23.0"), "3.0.24")
        self.assertEqual(cb._bump_version("2.61.1"), "2.61.2")
        self.assertEqual(cb._bump_version("1"), "2")
        # No parseable leading number → None (caller treats as inconclusive).
        self.assertIsNone(cb._bump_version(None))
        self.assertIsNone(cb._bump_version("abc"))

    def test_replay_generic_fallback_synthesizes_not_placeholder(self):
        """The generic fixture (no app-specific one) must NOT surface a fixed
        placeholder like the old 9.9.9 — it derives a believable next version
        from the deployed build."""
        from demo import claude_bridge

        with patch("demo.claude_bridge.time.sleep"):
            out = claude_bridge.check_version(
                "Acme Widget", "3.0.23.0", None, mode="replay",
                slug="acme-widget",  # no specific fixture -> generic fallback
            )
        self.assertEqual(out["latest_version"], "3.0.24")
        self.assertTrue(out["is_newer"])
        self.assertNotEqual(out["latest_version"], "9.9.9")

    def test_replay_generic_fallback_no_version_is_inconclusive(self):
        """With no deployed version to bump, the generic fallback must not
        invent a number — it reports not-newer rather than a placeholder."""
        from demo import claude_bridge

        with patch("demo.claude_bridge.time.sleep"):
            out = claude_bridge.check_version(
                "Mystery App", None, None, mode="replay", slug="mystery-app",
            )
        self.assertFalse(out["is_newer"])
        self.assertIsNone(out["latest_version"])


# --- Catalog ↔ Intune row enrichment ---------------------------------------

def _catalog_with_chain():
    entry = CatalogEntry(
        id="7-zip", type="msi",
        install_command_template="msiexec /i {installer_filename} /qn",
        canonical_download_url="https://www.7-zip.org/a/7z2408-x64.msi",
        supersedence={"mode": "generic", "line": "7-zip"},
        verified_versions=[
            {"product_version": "24.08", "status": "newest",
             "verified_intune_app_id": "app-new"},
            {"product_version": "23.01", "status": "superseded",
             "verified_intune_app_id": "app-old"},
            {"product_version": "22.01", "status": "superseded",
             "verified_intune_app_id": "app-older"},
        ],
    )
    return Catalog(entries=[entry]), entry


class TestIntuneViewEnrichment(unittest.TestCase):
    def test_find_entry_for_app_id(self):
        from demo import intune_view

        catalog, entry = _catalog_with_chain()
        found, row = intune_view.find_entry_for_app_id(catalog, "app-old")
        self.assertIs(found, entry)
        self.assertEqual(row["product_version"], "23.01")
        self.assertEqual(intune_view.find_entry_for_app_id(catalog, "nope"), (None, None))

    def test_version_state_current_and_superseded_rank(self):
        from demo import intune_view

        _, entry = _catalog_with_chain()
        newest = entry.verified_versions[0]
        most_recent_superseded = entry.verified_versions[1]
        older_superseded = entry.verified_versions[2]
        self.assertEqual(intune_view._version_state_for(entry, newest), "current")
        self.assertEqual(intune_view._version_state_for(entry, most_recent_superseded), "N-1")
        self.assertEqual(intune_view._version_state_for(entry, older_superseded), "N-2")

    def test_newest_is_current_even_when_pending(self):
        # Regression: a freshly-published newest version (status 'pending', not
        # yet device-confirmed) must still rank as "current", not "pending".
        from demo import intune_view
        from autopackager.utils.installer_catalog import Catalog, CatalogEntry

        entry = CatalogEntry(
            id="7-zip", type="msi", install_command_template="x",
            verified_versions=[
                {"product_version": "26.01.00.0", "status": "pending",
                 "verified_intune_app_id": "app-new"},
                {"product_version": "26.00.00.0", "status": "superseded",
                 "verified_intune_app_id": "app-old"},
            ],
        )
        newest = entry.verified_versions[0]
        older = entry.verified_versions[1]
        self.assertEqual(intune_view._version_state_for(entry, newest), "current")
        self.assertEqual(intune_view._version_state_for(entry, older), "N-1")

    def test_enrich_apps_ranks_deployed_versions(self):
        # Version state is derived by ranking DEPLOYED apps in a product line by
        # their Graph displayVersion — reliable without install confirmation. A
        # version known only to the catalog but not deployed is NOT "N-1" (that's
        # "update available", handled separately); N-1/N-2 describe the estate.
        from demo import intune_view

        catalog, _ = _catalog_with_chain()
        apps = [
            {"id": "app-old", "name": "7-Zip", "version": "23.01"},
            {"id": "app-older", "name": "7-Zip", "version": "22.01"},
            {"id": "unmanaged", "name": "Other", "version": "1.0"},
        ]
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=catalog):
            intune_view._enrich_apps(apps)
        by_id = {a["id"]: a for a in apps}
        self.assertEqual(by_id["app-old"]["catalog_entry_id"], "7-zip")
        # Ranked by deployed version: 23.01 = Latest, 22.01 = N-1.
        self.assertEqual(by_id["app-old"]["version_state"], "current")
        self.assertEqual(by_id["app-older"]["version_state"], "N-1")
        self.assertTrue(by_id["app-old"]["source_url_known"])
        # A lone unmanaged app is the only one in its line → Latest.
        self.assertIsNone(by_id["unmanaged"]["catalog_entry_id"])
        self.assertEqual(by_id["unmanaged"]["version_state"], "current")
        self.assertFalse(by_id["unmanaged"]["source_url_known"])

    def test_enrich_apps_ranks_by_name_without_catalog_or_verified_versions(self):
        # The reliability fix: in a device-less tenant with NO verified_versions
        # and NO catalog match, two deployed versions of the same product still
        # rank correctly by display name + deployed version (the old path
        # defaulted both to "current").
        from demo import intune_view
        from autopackager.utils.installer_catalog import Catalog

        apps = [
            {"id": "v1", "name": "VLC media player", "version": "3.0.20.0"},
            {"id": "v2", "name": "VLC media player", "version": "3.0.23.0"},
            {"id": "np", "name": "Notepad++", "version": "8.9.6"},
        ]
        with patch("autopackager.utils.installer_catalog.load_catalog",
                   return_value=Catalog(entries=[])):
            intune_view._enrich_apps(apps)
        by_id = {a["id"]: a for a in apps}
        self.assertEqual(by_id["v2"]["version_state"], "current")  # 3.0.23 = Latest
        self.assertEqual(by_id["v1"]["version_state"], "N-1")      # 3.0.20 = N-1
        self.assertEqual(by_id["np"]["version_state"], "current")  # lone product

    def test_enrich_apps_groups_dedupe_suffix(self):
        # The deploy agent appends '_01' on a duplicate publish, giving two apps
        # different displayNames ("VLC media player" / "VLC media player_01").
        # They must still group as one product line so they rank (this was a live
        # bug: both showed Latest).
        from demo import intune_view
        from autopackager.utils.installer_catalog import Catalog

        apps = [
            {"id": "a", "name": "VLC media player", "version": "3.0.20.0"},
            {"id": "b", "name": "VLC media player_01", "version": "3.0.23.0"},
        ]
        with patch("autopackager.utils.installer_catalog.load_catalog",
                   return_value=Catalog(entries=[])):
            intune_view._enrich_apps(apps)
        by_id = {a["id"]: a for a in apps}
        self.assertEqual(by_id["b"]["version_state"], "current")  # 3.0.23 = Latest
        self.assertEqual(by_id["a"]["version_state"], "N-1")      # 3.0.20 = N-1

    def test_normalize_product_name_strips_version_arch_dedupe(self):
        from demo import intune_view as iv
        self.assertEqual(iv._normalize_product_name("VLC media player_01"), "vlc media player")
        self.assertEqual(iv._normalize_product_name("Python 3.14.5 (64-bit)"), "python")
        self.assertEqual(iv._normalize_product_name("7-Zip 24.08 (x64)"), "7-zip")
        # 'v2' with no dotted version is part of the product name, not a version.
        self.assertEqual(iv._normalize_product_name("AWS Command Line Interface v2"),
                         "aws command line interface v2")

    def test_aggregate_install_counts(self):
        from demo import intune_view as iv
        statuses = [
            {"installState": "installed"}, {"installState": "installed"},
            {"installState": "failed"}, {"installState": "pending"},
            {"installState": "notApplicable"}, {"installState": "unknown"},
        ]
        agg = iv._aggregate_install_counts(statuses)
        self.assertEqual(agg["installed"], 2)
        self.assertEqual(agg["failed"], 1)
        self.assertEqual(agg["pending"], 1)
        self.assertEqual(agg["not_applicable"], 1)
        self.assertEqual(agg["total"], 6)
        self.assertEqual(iv._aggregate_install_counts([])["total"], 0)

    def test_enrich_apps_clean_flag(self):
        # clean = 0 confirmed installs; None when counts are unavailable.
        from demo import intune_view
        from autopackager.utils.installer_catalog import Catalog

        apps = [
            {"id": "a", "name": "Old App", "version": "1.0", "installed": 0},
            {"id": "b", "name": "Busy App", "version": "2.0", "installed": 3},
            {"id": "c", "name": "Unknown App", "version": "3.0"},  # no count fetched
        ]
        with patch("autopackager.utils.installer_catalog.load_catalog",
                   return_value=Catalog(entries=[])):
            intune_view._enrich_apps(apps)
        by_id = {a["id"]: a for a in apps}
        self.assertIs(by_id["a"]["clean"], True)
        self.assertIs(by_id["b"]["clean"], False)
        self.assertIsNone(by_id["c"]["clean"])


# --- Upgrade-job metadata builders -----------------------------------------

class TestUpgradeMetadata(unittest.TestCase):
    def _analysis(self):
        from demo.intake import Analysis

        return Analysis(
            kind="msi", path="/tmp/7z2408-x64.msi", filename="7z2408-x64.msi",
            branch="hit", catalog_entry_id="7-zip", version="24.08",
            product_name="7-Zip", publisher="Igor Pavlov",
        )

    def test_supersedence_action_shape_includes_old_app_id(self):
        from demo import intake

        catalog, _ = _catalog_with_chain()
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=catalog):
            action = intake._build_supersedence_action(self._analysis(), "app-old", "7-zip")
        # Keys the deployment agent's _apply_supersedence consumes.
        self.assertIn("superseded_intune_app_ids", action)
        self.assertIn("demoted_records", action)
        # The known old app id is always linked (forces a fresh app + the link).
        self.assertIn("app-old", action["superseded_intune_app_ids"])

    def test_supersedence_action_skips_same_version_sibling(self):
        # Regression: re-publishing 26.01 when a 26.01 sibling already exists
        # must link ONLY to the strictly-older 26.00, never the same-version
        # sibling (which would create a spurious self-relationship).
        from demo import intake
        from demo.intake import Analysis
        from autopackager.utils.installer_catalog import Catalog, CatalogEntry

        entry = CatalogEntry(
            id="7-zip", type="msi", install_command_template="x",
            supersedence={"mode": "generic", "line": "7-zip"},
            verified_versions=[
                {"product_version": "26.00.00.0", "status": "superseded",
                 "verified_intune_app_id": "app-old"},
                {"product_version": "26.01.00.0", "status": "pending",
                 "verified_intune_app_id": "app-sibling"},
            ],
        )
        analysis = Analysis(
            kind="msi", path="/tmp/7z2601-x64.msi", filename="7z2601-x64.msi",
            branch="hit", catalog_entry_id="7-zip", version="26.01.00.0",
            product_name="7-Zip", publisher="Igor Pavlov",
        )
        catalog = Catalog(entries=[entry])
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=catalog):
            action = intake._build_supersedence_action(analysis, "app-sibling", "7-zip")
        self.assertIn("app-old", action["superseded_intune_app_ids"])
        self.assertNotIn("app-sibling", action["superseded_intune_app_ids"])

    def test_resolve_upgrade_assignment_test_scope(self):
        from demo import intake

        with patch("demo.intake._ring0_group_id", return_value="grp-ring0"):
            block = intake._resolve_upgrade_assignment("app-old", "test")
        self.assertEqual(block["group_ids"], ["grp-ring0"])
        self.assertFalse(block["auto_update_superseded"])
        self.assertEqual(block["scope_label"], "Test ring only")

    def test_resolve_upgrade_assignment_all_scope_mirrors_old_groups(self):
        from demo import intake

        with patch("demo.intake._old_app_group_ids", return_value=["g1", "g2"]):
            block = intake._resolve_upgrade_assignment("app-old", "all")
        self.assertEqual(block["group_ids"], ["g1", "g2"])
        self.assertTrue(block["auto_update_superseded"])
        self.assertEqual(block["scope_label"], "All existing users")

    def test_resolve_upgrade_assignment_all_scope_falls_back_to_ring0(self):
        from demo import intake

        with patch("demo.intake._old_app_group_ids", return_value=[]), \
             patch("demo.intake._ring0_group_id", return_value="grp-ring0"):
            block = intake._resolve_upgrade_assignment("app-old", "all")
        self.assertEqual(block["group_ids"], ["grp-ring0"])
        self.assertTrue(block["auto_update_superseded"])


# --- Deployment-agent demo-scope branch ------------------------------------

class TestAssignDemoScope(unittest.TestCase):
    def setUp(self):
        from autopackager.agents.deployment.deployment_agent import DeploymentAgent

        cfg = {"deployment_rings": [
            {"ring_id": "ring0", "name": "Ring 0 - IT Pilot", "entra_group_id": "grp-ring0"},
        ]}
        with patch("autopackager.agents.deployment.deployment_agent.get_config", return_value=cfg):
            self.agent = DeploymentAgent()
        self.package = Mock()
        self.package.id = 100

    def test_all_scope_assigns_required_without_autoupdate_settings(self):
        # `autoUpdateSettings` is an AVAILABLE-intent-only assignment block;
        # attaching it to a `required` assignment makes Graph 400. We deploy
        # upgrades as `required`, where the upgrade is driven by the
        # mobileAppSupersedence(update) relationship — so no settings are sent
        # even when auto_update_superseded is requested.
        graph = Mock()
        with patch.object(self.agent, "_get_graph_client", return_value=graph), \
             patch.object(self.agent, "_create_deployment_record"):
            label = self.agent._assign_demo_scope(
                "app-new", self.package,
                {"group_ids": ["g1"], "auto_update_superseded": True,
                 "scope_label": "All existing users"},
            )
        graph.assign_app_to_group.assert_called_once()
        _, kwargs = graph.assign_app_to_group.call_args
        self.assertIsNone(kwargs["settings"])
        self.assertEqual(kwargs["intent"], "required")
        graph.win32_auto_update_settings.assert_not_called()
        self.assertIn("All existing users", label)

    def test_no_settings_for_test_scope(self):
        graph = Mock()
        with patch.object(self.agent, "_get_graph_client", return_value=graph), \
             patch.object(self.agent, "_create_deployment_record"):
            self.agent._assign_demo_scope(
                "app-new", self.package,
                {"group_ids": ["grp-ring0"], "auto_update_superseded": False,
                 "scope_label": "Test ring only"},
            )
        _, kwargs = graph.assign_app_to_group.call_args
        self.assertIsNone(kwargs["settings"])


# --- Duplicate display-name suffixing --------------------------------------

class TestDedupeDisplayName(unittest.TestCase):
    def setUp(self):
        from autopackager.agents.deployment.deployment_agent import DeploymentAgent

        cfg = {"deployment_rings": []}
        with patch("autopackager.agents.deployment.deployment_agent.get_config", return_value=cfg):
            self.agent = DeploymentAgent()

    def test_appends_suffix_on_exact_name_collision(self):
        graph = Mock()
        graph.get_win32_apps.return_value = {
            "value": [{"displayName": "7-Zip 26.01 (x64 edition)"}]
        }
        out = self.agent._dedupe_display_name(graph, "7-Zip 26.01 (x64 edition)")
        self.assertEqual(out, "7-Zip 26.01 (x64 edition)_01")

    def test_next_free_suffix(self):
        graph = Mock()
        graph.get_win32_apps.return_value = {"value": [
            {"displayName": "Foo 1.0"}, {"displayName": "Foo 1.0_01"},
        ]}
        self.assertEqual(self.agent._dedupe_display_name(graph, "Foo 1.0"), "Foo 1.0_02")

    def test_no_suffix_when_unique(self):
        graph = Mock()
        graph.get_win32_apps.return_value = {"value": [{"displayName": "Other"}]}
        self.assertEqual(
            self.agent._dedupe_display_name(graph, "7-Zip 26.01 (x64 edition)"),
            "7-Zip 26.01 (x64 edition)",
        )


# --- Uninstall retry ladder -------------------------------------------------

class TestUninstallLadder(unittest.TestCase):
    def _validator(self):
        from autopackager.agents.testing.local_install_validator import LocalInstallValidator
        return LocalInstallValidator(config={})

    def test_candidates_include_productcode_and_shipped(self):
        v = self._validator()
        pkg = Mock()
        pkg.uninstall_command = "msiexec /x {SHIPPEDGUID} /qn"
        discovered = {
            "quiet_uninstall": None,
            "uninstall_string": "MsiExec.exe /X{23170F69-40C1-2702-2600-000001000000}",
            "key_leaf": "{23170F69-40C1-2702-2600-000001000000}",
        }
        cands = v._uninstall_candidates(pkg, discovered)
        # Discovered msiexec string gets /qn appended and is tried first.
        self.assertIn("/qn", cands[0].lower())
        # A ProductCode-derived uninstall from the real ARP key is present.
        self.assertTrue(any("2600-000001000000" in c for c in cands))
        # The shipped command is kept as a backstop, capped at 5.
        self.assertTrue(any("{SHIPPEDGUID}" in c for c in cands))
        self.assertLessEqual(len(cands), 5)

    def test_ladder_records_working_non_shipped_command(self):
        v = self._validator()
        pkg = Mock()
        pkg.uninstall_command = "shipped /x"          # last in the ladder
        discovered = {"quiet_uninstall": "winner /qn", "uninstall_string": None,
                      "key_leaf": "", "key_path": "HKLM\\X", "view32": False}
        result = {"log": [], "uninstalled": False, "corrected_uninstall_command": None}
        with patch.object(v, "_run", return_value=(0, "")), \
             patch.object(v, "_confirm_removed", return_value=True):
            v._attempt_uninstall(pkg, discovered, result)
        self.assertTrue(result["uninstalled"])
        self.assertEqual(result["corrected_uninstall_command"], "winner /qn")

    def test_ladder_retries_past_a_failure(self):
        v = self._validator()
        pkg = Mock()
        pkg.uninstall_command = "shipped /x"
        discovered = {"quiet_uninstall": "winner /qn", "uninstall_string": None,
                      "key_leaf": "", "key_path": "HKLM\\X", "view32": False}
        result = {"log": [], "uninstalled": False, "corrected_uninstall_command": None}
        # First candidate doesn't remove it; second does → it must keep trying.
        with patch.object(v, "_run", return_value=(0, "")), \
             patch.object(v, "_confirm_removed", side_effect=[False, True]) as conf:
            v._attempt_uninstall(pkg, discovered, result)
        self.assertTrue(result["uninstalled"])
        self.assertEqual(conf.call_count, 2)


# --- Soft concurrent-upgrade guard -----------------------------------------

class TestInflightUpgradeGuard(unittest.TestCase):
    def _catalog(self):
        from autopackager.utils.installer_catalog import Catalog, CatalogEntry
        return Catalog(entries=[CatalogEntry(
            id="7-zip", type="msi", install_command_template="x",
            verified_versions=[{"product_version": "26.00.00.0", "status": "superseded",
                                "verified_intune_app_id": "app-old"}],
        )])

    def _job(self, state_value):
        j = Mock()
        j.id = 42
        j.state = Mock()
        j.state.value = state_value
        j.job_metadata = {"_upgrade": {"old_app_id": "app-old", "scope": "test"}}
        return j

    def test_detects_inflight_upgrade(self):
        from demo import router
        engine = Mock()
        engine.get_all_jobs.return_value = [self._job("deploying")]
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=self._catalog()), \
             patch("autopackager.orchestration.engine.OrchestrationEngine", return_value=engine):
            self.assertEqual(router._inflight_upgrade_for_app("app-old"), 42)

    def test_completed_upgrade_is_not_inflight(self):
        from demo import router
        engine = Mock()
        engine.get_all_jobs.return_value = [self._job("completed")]
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=self._catalog()), \
             patch("autopackager.orchestration.engine.OrchestrationEngine", return_value=engine):
            self.assertIsNone(router._inflight_upgrade_for_app("app-old"))


if __name__ == "__main__":
    unittest.main()


class TestNoDuplicateUpgrade(unittest.TestCase):
    """The upgrade/version-check must not offer a version that already exists in
    the tenant (no duplicate apps)."""

    def test_deployed_versions_for_app(self):
        from demo import intune_view
        view = {"apps": [
            {"id": "a", "version": "3.0.20.0", "product_line": "name:vlc media player"},
            {"id": "b", "version": "3.0.23.0", "product_line": "name:vlc media player"},
            {"id": "c", "version": "8.9", "product_line": "name:notepad"},
        ]}
        with patch("demo.intune_view.get_apps_view_cached", return_value=view):
            self.assertCountEqual(
                intune_view.deployed_versions_for_app("a"), ["3.0.20.0", "3.0.23.0"])
            self.assertEqual(intune_view.deployed_versions_for_app("zzz"), [])
            self.assertEqual(intune_view.deployed_versions_for_app(None), [])

    def test_already_deployed_version_is_not_offered(self):
        from demo import router
        body = {"app_label": "VLC media player", "current_version": "3.0.23.0", "mode": "replay"}
        with patch("demo.router._live_app_ids", return_value=None), \
             patch("autopackager.utils.installer_catalog.load_catalog",
                   return_value=Catalog(entries=[])), \
             patch("demo.claude_bridge.check_version",
                   return_value={"latest_version": "3.0.23", "is_newer": True,
                                 "download_url": "u", "current_version": "3.0.23.0"}), \
             patch("demo.intune_view.deployed_versions_for_app",
                   return_value=["3.0.23.0", "3.0.20.0"]):
            res = router._check_version_sync(body, "app-x")
        self.assertFalse(res["is_newer"])
        self.assertTrue(res.get("already_deployed"))

    def test_genuinely_newer_version_still_offered(self):
        from demo import router
        body = {"app_label": "VLC media player", "current_version": "3.0.20.0", "mode": "replay"}
        with patch("demo.router._live_app_ids", return_value=None), \
             patch("autopackager.utils.installer_catalog.load_catalog",
                   return_value=Catalog(entries=[])), \
             patch("demo.claude_bridge.check_version",
                   return_value={"latest_version": "3.0.23", "is_newer": True,
                                 "download_url": "u", "current_version": "3.0.20.0"}), \
             patch("demo.intune_view.deployed_versions_for_app",
                   return_value=["3.0.20.0"]):
            res = router._check_version_sync(body, "app-x")
        self.assertTrue(res["is_newer"])
        self.assertFalse(res.get("already_deployed"))

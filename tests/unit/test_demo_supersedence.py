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
        self.assertEqual(s["autoUpdateSettings"]["autoUpdateSupersededApps"], "enabled")

    def test_win32_auto_update_settings_disabled(self):
        s = GraphAPIClient.win32_auto_update_settings(False)
        self.assertEqual(s["autoUpdateSettings"]["autoUpdateSupersededApps"], "notConfigured")

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
        self.assertEqual(out["latest_version"], "24.08")

    def test_decide_is_newer_prefers_version_compare(self):
        from demo import claude_bridge

        # Model wrongly says not-newer, but 24.08 > 23.01 → trust the compare.
        self.assertTrue(claude_bridge._decide_is_newer("24.08", "23.01", False))
        # Equal versions are not newer even if the model claims so.
        self.assertFalse(claude_bridge._decide_is_newer("23.01", "23.01", True))
        # Unparseable current → fall back to the model's claim.
        self.assertTrue(claude_bridge._decide_is_newer("24.08", None, True))


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

    def test_enrich_apps_matches_and_defaults(self):
        from demo import intune_view

        catalog, _ = _catalog_with_chain()
        apps = [
            {"id": "app-old", "name": "7-Zip", "version": "23.01"},
            {"id": "unmanaged", "name": "Other", "version": "1.0"},
        ]
        with patch("autopackager.utils.installer_catalog.load_catalog", return_value=catalog):
            intune_view._enrich_apps(apps)
        self.assertEqual(apps[0]["catalog_entry_id"], "7-zip")
        self.assertEqual(apps[0]["version_state"], "N-1")
        self.assertTrue(apps[0]["source_url_known"])
        # Unmatched app gets safe defaults so the refresh gesture still works.
        self.assertIsNone(apps[1]["catalog_entry_id"])
        self.assertEqual(apps[1]["version_state"], "current")
        self.assertFalse(apps[1]["source_url_known"])


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

    def test_auto_update_settings_passed_for_all_scope(self):
        graph = Mock()
        graph.win32_auto_update_settings.return_value = {"sentinel": True}
        with patch.object(self.agent, "_get_graph_client", return_value=graph), \
             patch.object(self.agent, "_create_deployment_record"):
            label = self.agent._assign_demo_scope(
                "app-new", self.package,
                {"group_ids": ["g1"], "auto_update_superseded": True,
                 "scope_label": "All existing users"},
            )
        graph.assign_app_to_group.assert_called_once()
        _, kwargs = graph.assign_app_to_group.call_args
        self.assertEqual(kwargs["settings"], {"sentinel": True})
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


if __name__ == "__main__":
    unittest.main()

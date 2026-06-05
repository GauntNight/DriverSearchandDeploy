"""Unit tests for the unmanaged-software delta (installed but not packaged)."""

import unittest
from unittest.mock import Mock

from autopackager.services import software_delta as sd
from autopackager.utils.installer_catalog import Catalog, CatalogEntry


CONFIG = {
    "software_delta": {
        "microsoft_os_components": [
            r"^Microsoft Edge($| WebView2)", r"^Microsoft OneDrive",
            r"^Microsoft Visual C\+\+ \d{4}",
        ],
        "ignore_patterns": [r"^KB\d+", "Security Update"],
    }
}


class TestNormalize(unittest.TestCase):
    def test_strips_arch_edition_and_version(self):
        self.assertEqual(sd.normalize_name("7-Zip 26.01 (x64 edition)"), "7-zip")
        self.assertEqual(sd.normalize_name("Python 3.12.10 (64-bit)"), "python")
        self.assertEqual(sd.normalize_name("Microsoft Visual Studio Code (User)"),
                         "microsoft visual studio code")

    def test_publisher_variants_collapse(self):
        self.assertEqual(sd.normalize_publisher("RealNetworks, Inc."),
                         sd.normalize_publisher("Realnetworks"))
        self.assertEqual(sd.normalize_publisher("GitHub, Inc."), "github")

    def test_clean_publisher_extracts_cn_from_dn(self):
        # Intune detectedApps reports the signing-cert DN as publisher.
        self.assertEqual(
            sd.clean_publisher("CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, C=US"),
            "Microsoft Corporation")
        self.assertEqual(sd.clean_publisher("GitHub, Inc."), "GitHub, Inc.")

    def test_store_app_detection(self):
        self.assertTrue(sd._is_store_app("Microsoft.WindowsTerminal"))
        self.assertTrue(sd._is_store_app("MicrosoftCorporationII.QuickAssist"))
        # Must NOT catch real Win32 vendor names with a lowercase dotted suffix.
        self.assertFalse(sd._is_store_app("Node.js"))
        self.assertFalse(sd._is_store_app("Microsoft Azure CLI (64-bit)"))
        self.assertFalse(sd._is_store_app("MSTeams"))


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.os_pat = sd._compile(CONFIG["software_delta"]["microsoft_os_components"])
        self.ign = sd._compile(CONFIG["software_delta"]["ignore_patterns"])
        self.catalog = [(sd.normalize_name("7-Zip"), "7-zip"),
                        (sd.normalize_name("Git"), "git-for-windows")]

    def _c(self, row):
        return sd.classify(row, {"google chrome"}, self.catalog, self.os_pat, self.ign)

    def test_managed(self):
        self.assertEqual(self._c({"name": "Google Chrome"}), "managed")

    def test_system_component_is_os(self):
        self.assertEqual(self._c({"name": "Python 3.12 Test Suite", "system_component": True}),
                         "standard_os_component")

    def test_microsoft_os_pattern(self):
        self.assertEqual(self._c({"name": "Microsoft Edge WebView2 Runtime"}),
                         "standard_os_component")
        self.assertEqual(self._c({"name": "Microsoft Visual C++ 2015 Redistributable"}),
                         "standard_os_component")

    def test_microsoft_app_is_candidate_not_os(self):
        # Decision 2: Microsoft *apps* are candidates, only true OS bits are excluded.
        self.assertEqual(self._c({"name": "Microsoft Azure CLI"}), "unmanaged_candidate")

    def test_known_packageable(self):
        self.assertEqual(self._c({"name": "7-Zip 26.01 (x64 edition)"}), "known_packageable")

    def test_unmanaged_candidate(self):
        self.assertEqual(self._c({"name": "GitHub Desktop"}), "unmanaged_candidate")

    def test_ignored_update(self):
        self.assertEqual(self._c({"name": "KB5034567"}), "ignored")
        self.assertEqual(self._c({"name": "Security Update for Windows"}), "ignored")

    def test_store_app_bucket(self):
        self.assertEqual(self._c({"name": "Microsoft.WindowsTerminal"}), "store_app")


def _catalog():
    return Catalog(entries=[
        CatalogEntry(id="7-zip", type="msi", install_command_template="x", product_name_pattern="7-Zip"),
        CatalogEntry(id="git-for-windows", type="exe", pe_product_name="Git"),
    ])


class TestBuildDelta(unittest.TestCase):
    def test_dedup_counts_and_intune_unavailable(self):
        import autopackager.services.software_delta as mod

        # Graph: published Chrome (managed); detectedApps 403s; local ARP supplies rows.
        graph = Mock()
        graph.get_win32_apps.return_value = {"value": [{"displayName": "Google Chrome"}]}
        graph.list_detected_apps.side_effect = RuntimeError("403 Forbidden")

        arp_rows = [
            {"name": "Google Chrome", "publisher": "Google LLC", "version": "120", "system_component": False},
            {"name": "7-Zip 26.01 (x64 edition)", "publisher": "Igor Pavlov", "version": "26.01", "system_component": False},
            {"name": "Microsoft Edge WebView2 Runtime", "publisher": "Microsoft", "version": "1", "system_component": False},
            {"name": "GitHub Desktop", "publisher": "GitHub, Inc.", "version": "3.5", "system_component": False},
            {"name": "Some Runtime", "publisher": "X", "version": "1", "system_component": True},
        ]
        # Patch the ARP reader the service imports lazily.
        import autopackager.utils.arp as arp
        orig = arp.read_local_arp
        arp.read_local_arp = lambda: arp_rows
        orig_cat = mod.installer_catalog.load_catalog if hasattr(mod, "installer_catalog") else None
        try:
            import autopackager.utils.installer_catalog as ic
            real_load = ic.load_catalog
            ic.load_catalog = _catalog
            try:
                d = sd.build_delta(source="both", graph_client=graph, config=CONFIG)
            finally:
                ic.load_catalog = real_load
        finally:
            arp.read_local_arp = orig

        self.assertTrue(d["intune_unavailable"])
        c = d["counts"]
        self.assertEqual(c["managed"], 1)            # Chrome
        self.assertEqual(c["known_packageable"], 1)  # 7-Zip
        self.assertEqual(c["standard_os_component"], 2)  # WebView2 + system_component runtime
        self.assertEqual(c["unmanaged_candidate"], 1)    # GitHub Desktop
        self.assertEqual([r["name"] for r in d["candidates"]], ["GitHub Desktop"])


class TestDetectedAppsPagination(unittest.TestCase):
    def test_follows_next_link(self):
        from unittest.mock import patch
        from autopackager.utils.graph_client import GraphAPIClient
        import autopackager.utils.graph_client as gmod

        client = GraphAPIClient.__new__(GraphAPIClient)  # skip __init__/auth
        client._get_headers = lambda: {}
        client._raise_with_details = lambda r: None
        # First page via .get(); second page via requests.get(nextLink).
        client.get = lambda ep: {"value": [{"displayName": "A"}],
                                 "@odata.nextLink": "https://graph/next"}

        class _Resp:
            def json(self):
                return {"value": [{"displayName": "B"}]}

        with patch.object(gmod, "requests") as mreq:
            mreq.get.return_value = _Resp()
            out = client.list_detected_apps()
        self.assertEqual([a["displayName"] for a in out], ["A", "B"])


class TestSoftwareDeltaEndpoint(unittest.TestCase):
    def test_local_source_returns_buckets(self):
        from fastapi.testclient import TestClient
        from autopackager.web.api import app

        client = TestClient(app)
        r = client.get("/api/demo/intune/software-delta?source=local")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("counts", "candidates", "intune_unavailable", "source"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()

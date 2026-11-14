"""The registry is the single source for provider and FRED series lists."""
import json
from pathlib import Path
import re
import unittest

from lm import catalog
from lm.collect import cloud_feeds, live_api

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.providers = catalog.providers()
        self.ids = [p["id"] for p in self.providers]

    def test_collector_tables_derive_from_the_registry_in_order(self):
        self.assertEqual(list(cloud_feeds.PROVIDERS), self.ids)
        self.assertEqual(list(cloud_feeds.default_collectors()), self.ids)
        self.assertEqual(set(cloud_feeds.API_ACCESS), set(self.ids))
        self.assertEqual(set(cloud_feeds.TOTALS), set(self.ids) - {"fred"})
        self.assertEqual(cloud_feeds.EXTENSIONS, {p["id"] for p in self.providers if p["extension"]})

    def test_every_provider_declares_label_total_and_access(self):
        for provider in self.providers:
            with self.subTest(provider=provider["id"]):
                self.assertTrue(provider["label"])
                self.assertIn(provider["extension"], (True, False))
                self.assertEqual(set(provider["api_access"]), {"method", "authentication"})
                if provider["id"] == "fred":
                    self.assertIsNone(provider["total"])   # derived from the FRED series list
                else:
                    self.assertIsInstance(provider["total"], int)
                    self.assertGreater(provider["total"], 0)

    def test_fred_allow_list_is_exactly_the_registered_series(self):
        specs = live_api.load_catalog()
        self.assertEqual({s.series_id for s in specs}, set(live_api.ALLOWED_SERIES))
        self.assertEqual(len(specs), len(catalog.fred()["series"]))

    def test_sources_page_lists_exactly_the_registered_providers(self):
        html = (ROOT / "site" / "sources.html").read_text()
        block = html.split("const PROVIDERS = [", 1)[1].split("];", 1)[0]
        self.assertEqual(re.findall(r"\{id:'([a-z_]+)'", block), self.ids)
        buttons = re.findall(r'class="filter"[^>]*data-provider="([a-z_]+)"', html)
        self.assertEqual(buttons, ["all"] + self.ids)

    def test_duplicate_or_empty_registry_is_rejected(self):
        import tempfile
        content = json.loads(catalog.PATH.read_text())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "series.json"
            for mutate, code in (
                (lambda c: c["providers"].append(dict(c["providers"][0])), "empty_or_duplicate_provider_catalog"),
                (lambda c: c["fred"]["series"].append(dict(c["fred"]["series"][0])), "empty_or_duplicate_series_catalog"),
                (lambda c: c.update(schema_version=2), "unsupported_catalog_schema"),
            ):
                broken = json.loads(json.dumps(content))
                mutate(broken)
                path.write_text(json.dumps(broken))
                with self.assertRaisesRegex(ValueError, code):
                    catalog.load(path)


if __name__ == "__main__":
    unittest.main()

"""Model-input histories: full fetch, forward revision log, and no restricted data in the public store."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from lm import catalog
from lm.collect.live_api import FredClient
from lm.store import history

KEY = "k" * 32
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SECTION = {"observation_start": "2002-12-01", "series": [
    {"series_id": "WALCL", "persist": True},
    {"series_id": "WRESBAL", "persist": True},
    {"series_id": "SP500", "persist": False, "reason": "copyrighted"},
]}


class Source:
    """A fake FRED: metadata and observations per series, editable between runs."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.notes = {}
        self.updated = {sid: "2026-11-12 16:33:02-06" for sid in ("WALCL", "WRESBAL", "SP500")}
        self.rows = {
            "WALCL": [("2026-10-28", "6700000"), ("2026-11-04", "6750000"), ("2026-11-11", "6758867")],
            "WRESBAL": [("2026-10-28", "2950000"), ("2026-11-04", "."), ("2026-11-11", "2960603")],
            "SP500": [("2026-11-12", "7500.10"), ("2026-11-13", "7561.67")],
        }

    def __call__(self, endpoint, params):
        sid = params["series_id"]
        self.calls.append((endpoint, sid))
        if sid in self.fail:
            raise RuntimeError("boom " + KEY)
        if endpoint == "series":
            return {"seriess": [{"id": sid, "frequency": "Weekly, As of Wednesday", "units": "Millions of U.S. Dollars",
                                 "last_updated": self.updated[sid], "notes": self.notes.get(sid, "")}]}
        assert params["observation_end"] == "2026-11-14" and params["sort_order"] == "asc"
        return {"observations": [{"date": d, "value": v} for d, v in self.rows[sid]]}


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root, self.runtime = base / "data" / "history", base / "private" / "history"
        self.source = Source()

    def refresh(self, section=SECTION, now=NOW):
        client = FredClient(KEY, transport=self.source, sleeper=lambda _: None,
                            allowed=[s["series_id"] for s in section["series"]])
        return history.refresh(self.root, self.runtime, client=client, clock=lambda: now, section=section)

    def index(self):
        return json.loads((self.root / "index.json").read_text())["series"]

    def revisions(self):
        path = self.root / "revisions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_first_run_stores_full_history_and_logs_no_revisions(self):
        result = self.refresh()
        self.assertEqual((result["outcome"], result["total"], result["errors"], result["revisions"]), ("ok", 3, 0, 0))
        self.assertEqual(history.read_series(self.root, self.runtime, "WALCL"), self.source.rows["WALCL"])
        # A missing value stays missing: not zero, not carried forward.
        self.assertEqual(history.read_series(self.root, self.runtime, "WRESBAL")[1], ("2026-11-04", None))
        entry = self.index()["WALCL"]
        self.assertEqual((entry["observations"], entry["first_date"], entry["last_date"], entry["last_value"]),
                         (3, "2026-10-28", "2026-11-11", "6758867"))
        self.assertEqual(entry["retrieved_at"], "2026-11-14T12:00:00Z")
        self.assertEqual(self.revisions(), [])

    def test_restricted_series_never_reaches_the_public_store(self):
        self.refresh()
        public_files = {p.name for p in self.root.rglob("*") if p.is_file()}
        self.assertNotIn("SP500.csv", public_files)
        self.assertTrue((self.runtime / "series" / "SP500.csv").is_file())
        entry = self.index()["SP500"]
        self.assertFalse(entry["persisted"])
        self.assertNotIn("last_value", entry)
        everything_public = "".join(p.read_text() for p in self.root.rglob("*") if p.is_file())
        self.assertNotIn("7561.67", everything_public)
        self.assertNotIn("7500.10", everything_public)

    def test_series_moved_to_restricted_is_removed_from_the_public_store(self):
        self.refresh()
        moved = {**SECTION, "series": [{**s, "persist": False} if s["series_id"] == "WALCL" else s for s in SECTION["series"]]}
        self.refresh(section=moved)
        self.assertFalse((self.root / "series" / "WALCL.csv").exists())
        self.assertTrue((self.runtime / "series" / "WALCL.csv").is_file())

    def test_unchanged_source_is_not_downloaded_again(self):
        self.refresh()
        first = self.index()["WALCL"]
        self.source.calls.clear()
        later = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
        result = self.refresh(now=later)
        self.assertEqual(result["changed"], 0)
        self.assertNotIn(("series/observations", "WALCL"), self.source.calls)
        after = self.index()["WALCL"]
        # First-seen time survives; only the check time moves.
        self.assertEqual(after["retrieved_at"], first["retrieved_at"])
        self.assertEqual(after["checked_at"], "2026-11-14T18:00:00Z")

    def test_new_observation_is_not_a_revision_but_a_changed_past_value_is(self):
        self.refresh()
        self.source.rows["WALCL"] = [("2026-10-28", "6700000"), ("2026-11-04", "6751234"),
                                     ("2026-11-11", "6758867"), ("2026-11-18", "6760000")]
        self.source.updated["WALCL"] = "2026-11-19 16:33:02-06"
        later = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        client = FredClient(KEY, transport=lambda e, p: self.source(e, {**p, "observation_end": "2026-11-14"}),
                            sleeper=lambda _: None, allowed=[s["series_id"] for s in SECTION["series"]])
        result = history.refresh(self.root, self.runtime, client=client, clock=lambda: later, section=SECTION)
        self.assertEqual(result["revisions"], 1)
        revision = self.revisions()[0]
        self.assertEqual({k: revision[k] for k in ("series_id", "date", "kind", "old", "new")},
                         {"series_id": "WALCL", "date": "2026-11-04", "kind": "revised", "old": "6750000", "new": "6751234"})
        self.assertEqual(revision["detected_at"], "2026-11-20T00:00:00Z")
        self.assertEqual(self.index()["WALCL"]["last_date"], "2026-11-18")

    def test_formatting_difference_is_not_a_revision(self):
        old = [("2026-11-04", "1.50"), ("2026-11-11", "2")]
        new = [("2026-11-04", "1.5"), ("2026-11-11", "2.000"), ("2026-11-18", "3")]
        self.assertEqual(history.changes(old, new), [])

    def test_removed_filled_and_late_observations_are_logged(self):
        old = [("2026-10-28", "1"), ("2026-11-04", None), ("2026-11-11", "3")]
        new = [("2026-10-21", "0"), ("2026-11-04", "2"), ("2026-11-11", "3")]
        kinds = {(c["date"], c["kind"]) for c in history.changes(old, new)}
        self.assertEqual(kinds, {("2026-10-21", "added_late"), ("2026-10-28", "removed"), ("2026-11-04", "revised")})

    def test_one_failing_series_keeps_its_last_good_file_and_does_not_block_others(self):
        self.refresh()
        before = (self.root / "series" / "WRESBAL.csv").read_bytes()
        self.source.fail.add("WRESBAL")
        self.source.rows["WALCL"].append(("2026-11-13", "1"))
        self.source.updated["WALCL"] = "later"
        result = self.refresh()
        self.assertEqual((result["outcome"], result["errors"]), ("partial", 1))
        entry = self.index()["WRESBAL"]
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["last_date"], "2026-11-11")            # last good description retained
        self.assertEqual((self.root / "series" / "WRESBAL.csv").read_bytes(), before)
        self.assertEqual(self.index()["WALCL"]["observations"], 4)

    def test_error_text_never_contains_the_key(self):
        self.source.fail.update({"WALCL", "WRESBAL", "SP500"})
        result = self.refresh()
        self.assertEqual(result["outcome"], "error")
        self.assertNotIn(KEY, (self.root / "index.json").read_text())

    def test_invalid_payloads_are_rejected_without_writing(self):
        cases = {
            "future_observation_rejected": [("2026-11-11", "1"), ("2026-11-21", "2")],
            "observations_not_strictly_ascending": [("2026-11-11", "1"), ("2026-11-04", "2")],
            "invalid_observation_value": [("2026-11-11", "abc")],
            "no_valid_observation": [("2026-11-11", ".")],
        }
        for code, rows in cases.items():
            with self.subTest(code=code):
                self.source.rows["WALCL"] = rows
                self.source.updated["WALCL"] = code
                self.refresh()
                self.assertEqual(self.index()["WALCL"]["error"], code)
                self.assertFalse((self.root / "series" / "WALCL.csv").exists())

    def test_series_whose_notes_prohibit_reproduction_cannot_be_persisted(self):
        self.source.notes["WALCL"] = "Reproduction of this index in any form is prohibited except with permission."
        self.refresh()
        self.assertEqual(self.index()["WALCL"]["error"], "restricted_series_cannot_be_persisted")
        self.assertFalse((self.root / "series" / "WALCL.csv").exists())

    def test_damaged_index_is_not_overwritten(self):
        self.root.mkdir(parents=True)
        (self.root / "index.json").write_text("{broken")
        with self.assertRaisesRegex(RuntimeError, "previous_history_index_invalid"):
            self.refresh()
        self.assertEqual((self.root / "index.json").read_text(), "{broken")

    def test_client_refuses_series_outside_the_history_registry(self):
        client = FredClient(KEY, transport=self.source, sleeper=lambda _: None, allowed=["WALCL"])
        with self.assertRaisesRegex(Exception, "series_not_allowed"):
            client.request("series", {"series_id": "VIXCLS"})
        self.assertEqual(self.source.calls, [])

    def test_store_cannot_be_pointed_at_code_or_research(self):
        repo = Path(__file__).resolve().parents[1]
        for bad in (repo / "lm" / "x", repo / "research" / "x", repo / "catalog"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    history.refresh(bad, self.runtime, client=object(), clock=lambda: NOW, section=SECTION)


class HistoryRegistryTests(unittest.TestCase):
    def test_price_series_are_registered_as_not_persisted(self):
        series = {s["series_id"]: s for s in catalog.history()["series"]}
        for sid in ("SP500", "NASDAQCOM", "CBBTCUSD"):
            self.assertFalse(series[sid]["persist"], sid)
            self.assertTrue(series[sid]["reason"])
        self.assertTrue(all(isinstance(s["persist"], bool) for s in series.values()))

    def test_live_poller_allow_list_still_excludes_prices(self):
        from lm.collect import live_api
        self.assertFalse(set(live_api.ALLOWED_SERIES) & {"SP500", "NASDAQCOM", "CBBTCUSD"})


if __name__ == "__main__":
    unittest.main()

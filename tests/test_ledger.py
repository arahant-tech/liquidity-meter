"""The verification record: sealed once, chained, scored from the first tradable observation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lm.ledger import chain, score, seal
from lm.ledger.chain import LedgerError

PROTOCOLS = {"primary-v1": {"id": "primary-v1", "status": "fixed"}}
PLAN = {"primary-v1": [{"asset": "BTC", "horizon_weeks": 8, "role": "claim"},
                       {"asset": "EQ", "horizon_weeks": 8, "role": "reference"}]}
ENV = {"GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": "c" * 40}
THURSDAY = datetime(2026, 11, 12, 22, 30, tzinfo=timezone.utc)      # after the H.4.1 release for Wed 11 Nov


def judgment(week="2026-11-11", kind="confirmed", signal=-0.4, **extra):
    bounds = [-1.5, -0.5, 0.5, 1.5]
    base = {"protocol": "primary-v1", "week": week, "kind": kind, "status": "ok", "signal": signal,
            "bucket": 1 + sum(signal > b for b in bounds), "bounds": bounds,
            "components": {"scalar_z": signal / 2, "vector_z": signal / 2},
            "inputs": {"sha256": "a" * 64}}
    base.update(extra)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.records = self.dir / seal.RECORDS

    def seal(self, item, when=THURSDAY, protocols=PROTOCOLS):
        return seal.seal(self.dir, item, protocols=protocols, clock=lambda: when, env=ENV)


class ChainTests(Base):
    def test_every_line_commits_to_the_previous_one(self):
        first = chain.append(self.records, {"a": 1})
        second = chain.append(self.records, {"a": 2})
        self.assertEqual((first["seq"], first["prev"], second["seq"]), (0, None, 1))
        self.assertEqual(second["prev"], chain.line_hash(chain.canonical(first)))
        self.assertEqual(chain.head(self.records), chain.line_hash(chain.canonical(second)))
        self.assertEqual(chain.read(self.records), [first, second])

    def test_any_edit_to_the_past_is_detected(self):
        for value in (1, 2, 3):
            chain.append(self.records, {"a": value})
        original = self.records.read_bytes()
        lines = original.decode().split("\n")
        tampered = {
            "value changed": "\n".join([lines[0].replace('"a":1', '"a":9')] + lines[1:]),
            "line removed": "\n".join(lines[1:]),
            "lines swapped": "\n".join([lines[1], lines[0]] + lines[2:]),
            "reformatted": "\n".join([lines[0].replace(",", ", ", 1)] + lines[1:]),
            "truncated mid-line": original.decode()[:-5],
        }
        for label, text in tampered.items():
            with self.subTest(label=label):
                self.records.write_text(text)
                with self.assertRaises(LedgerError):
                    chain.read(self.records)
                with self.assertRaises(LedgerError):
                    chain.append(self.records, {"a": 4})         # a broken chain is never extended
                self.assertFalse(chain.is_append_only(original, text.encode()))
        self.records.write_bytes(original)
        chain.append(self.records, {"a": 4})
        self.assertTrue(chain.is_append_only(original, self.records.read_bytes()))

    def test_rewriting_the_whole_chain_changes_the_head(self):
        for value in (1, 2):
            chain.append(self.records, {"a": value})
        attested_head = chain.head(self.records)
        self.records.unlink()
        for value in (9, 2):                                     # a consistent but different history
            chain.append(self.records, {"a": value})
        self.assertNotEqual(chain.head(self.records), attested_head)

    def test_caller_cannot_set_chain_fields(self):
        with self.assertRaisesRegex(LedgerError, "record_must_not_set_chain_fields"):
            chain.append(self.records, {"seq": 5})


class SealTests(Base):
    def test_record_carries_time_code_and_run(self):
        record, reason = self.seal(judgment())
        self.assertIsNone(reason)
        self.assertEqual(record["sealed_at"], "2026-11-12T22:30:00Z")
        self.assertEqual(record["code"], {"commit": "c" * 40, "repository": "owner/repo"})
        self.assertEqual(record["run_url"], "https://github.com/owner/repo/actions/runs/77")
        self.assertEqual((record["bucket"], record["seq"], record["prev"]), (3, 0, None))

    def test_a_week_is_confirmed_only_once_even_if_the_numbers_later_differ(self):
        self.seal(judgment(signal=-0.4))
        before = self.records.read_bytes()
        record, reason = self.seal(judgment(signal=1.9), when=THURSDAY + timedelta(days=3))   # revised inputs
        self.assertIsNone(record)
        self.assertEqual(reason, "already_confirmed")
        self.assertEqual(self.records.read_bytes(), before)

    def test_provisional_is_once_per_day_and_stops_after_confirmation(self):
        monday = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
        next_week = "2026-11-18"
        with self.assertRaisesRegex(LedgerError, "cannot_seal_a_week_that_has_not_happened"):
            self.seal(judgment(week=next_week, kind="provisional"), when=monday)
        wednesday = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
        self.assertIsNotNone(self.seal(judgment(week=next_week, kind="provisional"), when=wednesday)[0])
        self.assertEqual(self.seal(judgment(week=next_week, kind="provisional"), when=wednesday + timedelta(hours=6))[1],
                         "provisional_already_sealed_today")
        self.assertIsNotNone(self.seal(judgment(week=next_week, kind="provisional"), when=wednesday + timedelta(days=1))[0])
        self.assertIsNotNone(self.seal(judgment(week=next_week), when=wednesday + timedelta(days=1, hours=14))[0])
        self.assertEqual(self.seal(judgment(week=next_week, kind="provisional"), when=wednesday + timedelta(days=2))[1],
                         "already_confirmed")

    def test_missing_signal_is_recorded_as_unavailable_not_replaced(self):
        item = {"protocol": "primary-v1", "week": "2026-11-11", "kind": "confirmed", "status": "unavailable",
                "reason": "component_missing", "inputs": {"sha256": "a" * 64}}
        record, _ = self.seal(item)
        self.assertEqual(record["status"], "unavailable")
        self.assertNotIn("bucket", record)
        with self.assertRaisesRegex(LedgerError, "judgment_unavailable_must_not_carry_a_signal"):
            self.seal({**item, "week": "2026-11-04", "signal": 0.1})

    def test_only_a_fixed_unchanged_protocol_can_be_sealed_under(self):
        with self.assertRaisesRegex(LedgerError, "protocol_not_fixed"):
            self.seal(judgment(), protocols={})
        root = self.dir / "protocols"
        root.mkdir()
        (root / "P.md").write_text("fixed text")
        digest = hashlib.sha256(b"fixed text").hexdigest()
        registry = {"protocols": [{"id": "primary-v1", "status": "fixed", "document": "P.md", "sha256": digest},
                                  {"id": "candidate-x", "status": "draft", "document": "missing.md", "sha256": None}]}
        (root / "registry.json").write_text(json.dumps(registry))
        self.assertEqual(set(seal.load_protocols(root)), {"primary-v1"})          # drafts cannot be sealed under
        (root / "P.md").write_text("fixed text, quietly edited")
        with self.assertRaisesRegex(LedgerError, "protocol_document_changed_after_fixing"):
            seal.load_protocols(root)

    def test_invalid_judgments_are_refused(self):
        cases = {
            "judgment_week_must_be_a_wednesday": judgment(week="2026-11-12"),
            "judgment_bucket_disagrees_with_bounds": judgment(bucket=5),
            "judgment_bounds_invalid": judgment(bounds=[0.5, -0.5, 1, 2]),
            "judgment_signal_invalid": judgment(signal=float("nan"), bucket=3),
            "judgment_kind_invalid": judgment(kind="final"),
            "judgment_inputs_hash_missing": judgment(inputs={}),
            "judgment_has_unknown_fields": judgment(note="looks bullish"),
            "judgment_status_invalid": judgment(status="maybe"),
        }
        for code, item in cases.items():
            with self.subTest(code=code):
                with self.assertRaisesRegex(LedgerError, code):
                    self.seal(item)
        self.assertFalse(self.records.exists())

    def test_clock_cannot_run_backwards(self):
        self.seal(judgment())
        with self.assertRaisesRegex(LedgerError, "clock_went_backwards"):
            self.seal(judgment(week="2026-11-04"), when=THURSDAY - timedelta(days=1))


class ScoreTests(Base):
    def daily(self, start, days, value=lambda i: 100 + i, skip=()):
        first = datetime.fromisoformat(start)
        rows = []
        for i in range(days):
            day = (first + timedelta(days=i)).date().isoformat()
            rows.append((day, None if day in skip else str(value(i))))
        return rows

    def test_entry_is_the_first_observation_after_the_sealing_day(self):
        rows = self.daily("2026-11-10", 80)
        entry, exit_row = score.entry_exit(rows, "2026-11-12T22:30:00Z", 8)
        self.assertEqual(entry[0], "2026-11-13")                 # not the reference Wednesday, not the sealing day
        self.assertEqual(exit_row[0], "2027-01-08")              # 56 days later
        late = score.entry_exit(rows, "2026-11-13T00:05:00Z", 8)  # sealed after midnight UTC: one day later
        self.assertEqual(late[0][0], "2026-11-14")

    def test_missing_prices_are_skipped_not_filled(self):
        rows = self.daily("2026-11-10", 80, skip={"2026-11-13", "2026-11-14", "2027-01-11"})
        entry, exit_row = score.entry_exit(rows, "2026-11-12T22:30:00Z", 8)
        self.assertEqual(entry[0], "2026-11-15")
        self.assertEqual(exit_row[0], "2027-01-10")
        weekdays_only = [(d, v) for d, v in self.daily("2026-11-10", 80) if datetime.fromisoformat(d).weekday() < 5]
        entry, exit_row = score.entry_exit(weekdays_only, "2026-11-13T22:30:00Z", 8)   # sealed Friday
        self.assertEqual(entry[0], "2026-11-16")                 # Monday
        self.assertEqual(exit_row[0], "2027-01-11")

    def test_nothing_is_scored_before_the_horizon_is_observed(self):
        self.seal(judgment())
        short = {"BTC": self.daily("2026-11-10", 50), "EQ": self.daily("2026-11-10", 50)}
        self.assertEqual(score.due(self.dir, short, PLAN, clock=lambda: THURSDAY), [])
        self.assertFalse((self.dir / score.SCORES).exists())

    def test_score_keeps_return_and_dates_but_no_price_level(self):
        record, _ = self.seal(judgment())
        prices = {"BTC": self.daily("2026-11-10", 80, value=lambda i: 68000 + 100 * i), "EQ": []}
        later = datetime(2027, 1, 9, tzinfo=timezone.utc)
        added = score.due(self.dir, prices, PLAN, clock=lambda: later)
        self.assertEqual(len(added), 1)                           # the asset without prices is left pending
        item = added[0]
        self.assertEqual((item["asset"], item["role"], item["entry_date"], item["exit_date"], item["entry_lag_days"]),
                         ("BTC", "claim", "2026-11-13", "2027-01-08", 1))
        self.assertEqual(item["return"], round((68000 + 100 * 59) / (68000 + 100 * 3) - 1, 6))
        self.assertEqual(item["record"], seal.record_hash(record))
        text = (self.dir / score.SCORES).read_text()
        self.assertNotIn("68300", text)
        self.assertNotIn("73900", text)
        self.assertEqual(score.due(self.dir, prices, PLAN, clock=lambda: later), [])     # scored once

    def test_provisional_and_unavailable_records_are_not_scored(self):
        self.seal(judgment(kind="provisional"), when=datetime(2026, 11, 11, 12, tzinfo=timezone.utc))
        self.seal({"protocol": "primary-v1", "week": "2026-11-11", "kind": "confirmed", "status": "unavailable",
                   "reason": "component_missing", "inputs": {"sha256": "a" * 64}})
        prices = {"BTC": self.daily("2026-11-10", 80), "EQ": self.daily("2026-11-10", 80)}
        self.assertEqual(score.due(self.dir, prices, PLAN, clock=lambda: THURSDAY), [])


class ScorecardTests(Base):
    def build(self, weeks, signal_for, value_for):
        start = datetime(2026, 11, 11, tzinfo=timezone.utc)
        prices = {"BTC": [((start + timedelta(days=i)).date().isoformat(), str(value_for(i)))
                          for i in range(7 * weeks + 70)], "EQ": []}
        for w in range(weeks):
            week = start + timedelta(weeks=w)
            seal.seal(self.dir, judgment(week=week.date().isoformat(), signal=signal_for(w)), protocols=PROTOCOLS,
                      clock=lambda week=week: week + timedelta(days=1, hours=22), env=ENV)
        score.due(self.dir, prices, PLAN, clock=lambda: start + timedelta(weeks=weeks + 10))
        return score.scorecard(self.dir, PLAN, clock=lambda: start + timedelta(weeks=weeks + 10))

    def test_small_sample_reports_counts_only(self):
        card = self.build(20, lambda w: -2 + (w % 5), lambda i: 100 + i)
        asset = card["protocols"]["primary-v1"]["assets"][0]
        self.assertEqual((asset["scored"], asset["independent"], asset["enough_to_read"]), (20, 2, False))
        self.assertIsNone(asset["rank_correlation"])
        self.assertIsNone(asset["shift_null"])
        self.assertEqual(sum(b["n"] for b in asset["by_bucket"].values()), 20)
        self.assertEqual(card["protocols"]["primary-v1"]["assets"][1]["scored"], 0)

    def test_independent_count_divides_by_the_overlapping_horizon(self):
        card = self.build(60, lambda w: -2 + (w % 5), lambda i: 100 + i)
        asset = card["protocols"]["primary-v1"]["assets"][0]
        self.assertEqual((asset["scored"], asset["independent"], asset["enough_to_read"]), (60, 7, True))
        self.assertIsNotNone(asset["shift_null"])
        self.assertEqual(asset["shift_null"]["shifts"], 59)
        self.assertEqual(card["head"]["records"], chain.head(self.records))

    def test_rank_correlation_and_ties(self):
        self.assertAlmostEqual(score.rank_correlation([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]), 1.0)
        self.assertAlmostEqual(score.rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), -1.0)
        self.assertIsNone(score.rank_correlation([3, 3, 3, 3], [1, 2, 3, 4]))       # no variation: undefined, not zero
        self.assertAlmostEqual(score.rank_correlation([1, 1, 2, 2], [1, 2, 3, 4]), 0.894427, places=5)


class ProtocolRegistryTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "research" / "protocols"

    def setUp(self):
        self.registry = json.loads((self.ROOT / "registry.json").read_text())["protocols"]

    def test_fixed_protocols_match_their_documents_and_drafts_carry_no_hash(self):
        seal.load_protocols(self.ROOT)                     # raises if a fixed document was edited
        for entry in self.registry:
            with self.subTest(protocol=entry["id"]):
                self.assertIn(entry["status"], ("draft", "fixed"))
                self.assertTrue((self.ROOT / entry["document"]).is_file())
                if entry["status"] == "draft":
                    self.assertIsNone(entry["sha256"])
                    self.assertIsNone(entry["fixed_at"])
                else:
                    self.assertTrue(entry["fixed_at"])

    def test_scoring_assets_are_registered_histories_with_one_claim(self):
        from lm import catalog
        known = {s["series_id"] for s in catalog.history()["series"]}
        for entry in self.registry:
            assets = [item["asset"] for item in entry["scoring"]]
            self.assertTrue(set(assets) <= known, set(assets) - known)
            self.assertEqual(len(assets), len(set(assets)))
            self.assertEqual([item["role"] for item in entry["scoring"]].count("claim"), 1)
            self.assertTrue(all(item["role"] in ("claim", "reference") for item in entry["scoring"]))

    def test_draft_protocol_cannot_be_sealed_under(self):
        usable = seal.load_protocols(self.ROOT)
        drafts = [e["id"] for e in self.registry if e["status"] == "draft"]
        for protocol in drafts:
            self.assertNotIn(protocol, usable)
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(LedgerError, "protocol_not_fixed"):
                    seal.seal(temp, judgment(protocol=protocol), protocols=usable, clock=lambda: THURSDAY, env=ENV)


if __name__ == "__main__":
    unittest.main()

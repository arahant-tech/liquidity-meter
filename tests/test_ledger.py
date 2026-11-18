"""The verification record: sealed once, chained, scored from the first tradable observation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lm.ledger import chain, score, seal, tilt
from lm.ledger.chain import LedgerError
from lm.measure import quadrant
from lm.store import history

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

    def test_spread_outcome_is_a_log_return_difference_on_days_both_were_observed(self):
        import math
        plan = {"tilt-v1": [{"asset": "GROWTH", "minus": "MARKET", "horizon_weeks": 13, "role": "claim"},
                            {"asset": "MARKET", "horizon_weeks": 13, "role": "reference"}]}
        protocols = {"tilt-v1": {"id": "tilt-v1", "status": "fixed"}}
        self.seal(judgment(protocol="tilt-v1", signal=1.2), protocols=protocols)       # sealed Thu 12 Nov
        growth = self.daily("2026-11-10", 120, value=lambda i: 20000 + 40 * i)
        market = self.daily("2026-11-10", 120, value=lambda i: 6000 + 5 * i, skip={"2026-11-13"})
        added = score.due(self.dir, {"GROWTH": growth, "MARKET": market}, plan, clock=lambda: THURSDAY)
        claim = next(s for s in added if s["asset"] == "GROWTH")
        # Friday 13 Nov has no market value, so both legs enter on Saturday 14 Nov and leave 91 days later.
        self.assertEqual((claim["entry_date"], claim["exit_date"]), ("2026-11-14", "2027-02-13"))
        self.assertEqual((claim["minus"], claim["measure"]), ("MARKET", "log_return_difference"))
        g0, g1 = 20000 + 40 * 4, 20000 + 40 * 95
        m0, m1 = 6000 + 5 * 4, 6000 + 5 * 95
        self.assertAlmostEqual(claim["return"], math.log(g1 / g0) - math.log(m1 / m0), places=6)
        reference = next(s for s in added if s["asset"] == "MARKET")
        self.assertNotIn("minus", reference)
        self.assertAlmostEqual(reference["return"], m1 / m0 - 1, places=6)
        text = (self.dir / score.SCORES).read_text()
        for level in (str(g0), str(g1), str(m0), str(m1)):
            self.assertNotIn(level, text)                                              # dates and returns only
        self.assertEqual(score.due(self.dir, {"GROWTH": growth, "MARKET": market}, plan, clock=lambda: THURSDAY), [])

    def test_spread_waits_until_the_benchmark_is_available(self):
        plan = {"tilt-v1": [{"asset": "GROWTH", "minus": "MARKET", "horizon_weeks": 13, "role": "claim"}]}
        self.seal(judgment(protocol="tilt-v1"), protocols={"tilt-v1": {"id": "tilt-v1", "status": "fixed"}})
        growth = self.daily("2026-11-10", 120)
        self.assertEqual(score.due(self.dir, {"GROWTH": growth, "MARKET": []}, plan, clock=lambda: THURSDAY), [])
        self.assertEqual(score.due(self.dir, {"GROWTH": growth}, plan, clock=lambda: THURSDAY), [])

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


def wobble(i):
    """Reserves that move unevenly, so 13-week changes take many different values."""
    return 3_000_000 + 1_000 * ((7 * i * i + 13 * i) % 97 - 48)


def write_store(root, weeks=220, reserves=wobble, skip=()):
    """A small H.4.1 history in the store's own format, ending on Wednesday 2026-11-11."""
    last = datetime(2026, 11, 11).date()
    days = [(last - timedelta(weeks=weeks - 1 - i)).isoformat() for i in range(weeks)]
    levels = {"L": lambda i: 6_000_000, "R": reserves, "RRP": lambda i: 100_000, "FRRP": lambda i: 300_000,
              "TGA": lambda i: 800_000, "CUR": lambda i: 2_400_000}
    (Path(root) / "series").mkdir(parents=True, exist_ok=True)
    for key, sid in quadrant.SERIES.items():
        if key not in skip:
            rows = [(d, repr(float(levels[key](i)))) for i, d in enumerate(days)]
            (Path(root) / "series" / f"{sid}.csv").write_bytes(history.encode(rows))
    return days


class TiltJudgmentTests(Base):
    REAL = Path(__file__).resolve().parents[1] / "research" / "protocols"

    def setUp(self):
        super().setUp()
        self.store = self.dir / "history"

    def test_judgment_is_the_measurement_and_passes_the_seal_checks(self):
        days = write_store(self.store, reserves=lambda i: wobble(i) + (900_000 if i >= 215 else 0))
        made = tilt.build(self.store)
        self.assertEqual((made["protocol"], made["week"], made["kind"], made["status"]), ("tilt-v1", days[-1], "confirmed", "ok"))
        seal.validate(made)
        self.assertEqual((made["bucket"], made["components"]["tilt"]), (5, 1))
        self.assertEqual(made["bucket"], tilt.bucket_of(made["signal"], made["bounds"]))        # from the sealed numbers
        self.assertEqual(made["components"]["x_tilt"], 1)                                       # the Treasury account did not move
        self.assertEqual(made["inputs"]["series"], sorted(quadrant.SERIES.values()))
        self.assertNotIn("return", json.dumps(made))

    def test_a_value_on_a_boundary_goes_to_the_lower_bucket(self):
        self.assertEqual([tilt.bucket_of(v, [-1.0, 0.0, 1.0, 2.0]) for v in (-2, -1.0, 0.5, 1.0, 1.0001, 2.0, 9)], [1, 1, 3, 3, 4, 4, 5])

    def test_input_hash_follows_the_data_and_a_missing_input_stops_the_judgment(self):
        write_store(self.store)
        first = tilt.build(self.store)["inputs"]["sha256"]
        self.assertEqual(first, tilt.build(self.store)["inputs"]["sha256"])
        write_store(self.store, reserves=lambda i: 3_000_001)
        self.assertNotEqual(first, tilt.build(self.store)["inputs"]["sha256"])
        (self.store / "series" / "WDTGAL.csv").unlink()
        with self.assertRaisesRegex(LedgerError, "judgment_input_missing"):
            tilt.build(self.store)

    def test_without_enough_history_the_week_is_recorded_as_unavailable(self):
        write_store(self.store, weeks=60)
        made = tilt.build(self.store)
        self.assertEqual((made["status"], made["reason"]), ("unavailable", "not_enough_history_for_a_bucket"))
        seal.validate(made)

    def test_the_fixed_protocol_seals_a_week_once(self):
        write_store(self.store)
        protocols = seal.load_protocols(self.REAL)
        record, reason = seal.seal(self.dir, tilt.build(self.store), protocols=protocols, clock=lambda: THURSDAY, env=ENV)
        self.assertEqual((record["protocol"], record["week"], record["status"], reason), ("tilt-v1", "2026-11-11", "ok", None))
        again, reason = seal.seal(self.dir, tilt.build(self.store), protocols=protocols, clock=lambda: THURSDAY, env=ENV)
        self.assertEqual((again, reason), (None, "already_confirmed"))

    def test_command_seals_and_reports(self):
        import contextlib, io
        from lm.ledger import __main__ as cli
        write_store(self.store)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(["seal-tilt", "--dir", str(self.dir / "record"), "--history", str(self.store)])
        result = json.loads(out.getvalue())
        self.assertEqual((code, result["sealed"], result["week"]), (0, True, "2026-11-11"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(["seal-tilt", "--dir", str(self.dir / "record"), "--history", str(self.dir / "nowhere")])
        self.assertEqual((code, json.loads(out.getvalue())), (1, {"error": "judgment_input_missing"}))


class TamperTests(Base):
    """What a consistent-looking chain cannot hide."""

    def sealed(self, weeks=("2026-10-28", "2026-11-04", "2026-11-11")):
        for i, week in enumerate(weeks):
            when = datetime.fromisoformat(week).replace(tzinfo=timezone.utc) + timedelta(days=1, hours=22)
            self.seal(judgment(week=week, signal=-0.4 + i), when=when)
        return score.scorecard(self.dir, PLAN)

    def rewrite(self, change):
        rows = [json.loads(line) for line in self.records.read_text().strip().split("\n")]
        self.records.unlink()
        for row in rows:
            for key in ("seq", "prev", "schema_version"):
                row.pop(key)
            change(row)
            chain.append(self.records, row)

    def test_summary_carries_the_head_and_count_it_was_made_from(self):
        card = self.sealed()
        self.assertEqual(card["count"], {"records": 3, "scores": 0})
        self.assertEqual(card["head"]["records"], chain.head(self.records))

    def test_record_that_only_grew_still_matches_the_published_summary(self):
        card = self.sealed()
        self.seal(judgment(week="2026-11-18"), when=datetime(2026, 11, 19, 22, 30, tzinfo=timezone.utc))
        self.assertEqual(score.check_anchor(self.dir, card), {"records": 3})
        self.assertEqual(score.check_anchor(self.dir, {}), {})                                  # nothing published yet

    def test_a_rewritten_record_is_caught_even_though_its_own_chain_is_consistent(self):
        card = self.sealed()
        self.rewrite(lambda row: row.update(signal=1.2, bucket=4) if row["week"] == "2026-11-04" else None)
        self.assertEqual(len(chain.read(self.records)), 3)                                       # the chain itself is fine
        score.cross_check(self.dir)
        with self.assertRaisesRegex(LedgerError, "record_no_longer_matches_the_published_summary"):
            score.check_anchor(self.dir, card)

    def test_a_shortened_record_is_caught(self):
        card = self.sealed()
        lines = self.records.read_text().strip().split("\n")
        self.records.write_text("\n".join(lines[:2]) + "\n")
        with self.assertRaisesRegex(LedgerError, "record_no_longer_matches_the_published_summary"):
            score.check_anchor(self.dir, card)

    def test_an_edited_newest_line_is_caught_by_its_own_numbers(self):
        self.sealed()
        text = self.records.read_text()
        last = text.strip().split("\n")[-1]
        self.assertIn('"bucket":5', last)
        self.records.write_text(text.replace(last, last.replace('"bucket":5', '"bucket":4')))
        self.assertEqual(len(chain.read(self.records)), 3)                                       # nothing after it to vouch for it
        with self.assertRaisesRegex(LedgerError, "record_bucket_disagrees_with_its_bounds"):
            score.cross_check(self.dir)

    def test_a_score_for_a_record_that_is_gone_is_caught(self):
        self.seal(judgment())
        days = [(datetime(2026, 11, 10) + timedelta(days=i)).date().isoformat() for i in range(80)]
        prices = {"BTC": [(d, str(100 + i)) for i, d in enumerate(days)], "EQ": []}
        self.assertEqual(len(score.due(self.dir, prices, PLAN, clock=lambda: THURSDAY)), 1)
        score.cross_check(self.dir)
        self.rewrite(lambda row: row.update(signal=-0.3))                                      # same bucket, different record
        with self.assertRaisesRegex(LedgerError, "score_refers_to_a_record_that_is_not_there"):
            score.cross_check(self.dir)


class OnOffSummaryTests(Base):
    def test_tilt_summary_splits_scores_at_the_tilt_bucket(self):
        plan = {"tilt-v1": [{"asset": "G", "minus": "M", "horizon_weeks": 13, "role": "claim"}]}
        protocols = {"tilt-v1": {"id": "tilt-v1", "status": "fixed"}}
        days = [(datetime(2026, 10, 1) + timedelta(days=i)).date().isoformat() for i in range(160)]
        prices = {"G": [(d, str(1000 + 3 * i)) for i, d in enumerate(days)], "M": [(d, "500") for d in days]}
        for week, signal in (("2026-10-07", 1.2), ("2026-10-14", -0.4)):                         # bucket 4, then bucket 2
            when = datetime.fromisoformat(week).replace(tzinfo=timezone.utc) + timedelta(days=1, hours=22)
            self.seal(judgment(protocol="tilt-v1", week=week, signal=signal), when=when, protocols=protocols)
        score.due(self.dir, prices, plan, clock=lambda: THURSDAY)
        extras = {"tilt-v1": {"tilt_from_bucket": 4, "criteria": {"reviews_at_scored_weeks": [104]}}}
        card = score.scorecard(self.dir, plan, extras=extras)["protocols"]["tilt-v1"]
        self.assertEqual((card["on_weeks"], card["off_weeks"], card["criteria"]), (1, 1, {"reviews_at_scored_weeks": [104]}))
        split = card["assets"][0]["on_off"]
        self.assertEqual((split["on"]["n"], split["off"]["n"]), (1, 1))
        self.assertAlmostEqual(split["difference"], split["on"]["mean"] - split["off"]["mean"], places=6)
        plain = score.scorecard(self.dir, plan)["protocols"]["tilt-v1"]
        self.assertNotIn("on_off", plain["assets"][0])                                           # only where the registry asks for it


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
            named = set(assets) | {item["minus"] for item in entry["scoring"] if "minus" in item}
            self.assertTrue(named <= known, named - known)
            self.assertEqual(len(assets), len(set(assets)))
            self.assertEqual([item["role"] for item in entry["scoring"]].count("claim"), 1)
            self.assertTrue(all(item["role"] in ("claim", "reference") for item in entry["scoring"]))

    def test_tilt_protocol_is_fixed_with_the_studys_review_lines(self):
        study = json.loads((self.ROOT.parent / "studies" / "tilt-2026-11" / "results.json").read_text())["live_criteria"]
        entry = next(e for e in self.registry if e["id"] == "tilt-v1")
        self.assertEqual(entry["status"], "fixed")
        self.assertIn("tilt-v1", seal.load_protocols(self.ROOT))
        self.assertEqual(entry["tilt_from_bucket"], quadrant.TILT_FROM_BUCKET)
        criteria = entry["criteria"]
        self.assertEqual(criteria["reviews_at_scored_weeks"], study["reviews"])
        self.assertEqual(criteria["kill_at_or_below"], study["kill_lines"])
        self.assertEqual((criteria["pass_at_or_above"], criteria["pass_only_at_scored_weeks"]), (study["pass_line_at_260"], study["reviews"][-1]))
        self.assertEqual(criteria["min_group_weeks"], study["min_group_weeks"])
        self.assertEqual(tilt.PROTOCOL, "tilt-v1")

    def test_pipeline_saves_the_record_without_force_and_publishes_the_summary_last(self):
        text = (self.ROOT.parents[1] / ".github" / "workflows" / "pipeline.yml").read_text()
        order = [text.index(marker) for marker in (
            "ref: ledger", "lm.ledger verify --dir ledger --anchor data/public/ledger.json",
            "lm.ledger seal-tilt --dir ledger --history data/history", "lm.ledger score --dir ledger",
            "name: Save the verification record", "git push origin HEAD:ledger",
            "name: Publish the record summary", "lm.ledger scorecard --dir ledger --output data/public/ledger.json",
            "name: Save state to the data branch")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(text.count("lm.ledger scorecard"), 1)                                   # only after the record is saved
        self.assertIn("if: steps.record.outcome == 'success'", text)
        self.assertIn("if: steps.save_record.outcome == 'success'", text)
        record_part = text[text.index("name: Save the verification record"):text.index("name: Publish the record summary")]
        for forbidden in ("--force", "-f ", "rebase", "+HEAD", "reset"):
            self.assertNotIn(forbidden, record_part)

    def test_tilt_protocol_states_the_lines_the_study_derived(self):
        study = json.loads((self.ROOT.parent / "studies" / "tilt-2026-11" / "results.json").read_text())
        text = (self.ROOT / "PROTOCOL_tilt_v1.md").read_text().replace("−", "-")
        live = study["live_criteria"]
        self.assertEqual(live["reviews"], [104, 156, 208, 260])
        for line in live["kill_lines"]:
            self.assertIn(f"{line:.2f}", text)
        self.assertIn(f"+{live['pass_line_at_260']:.2f}", text)
        for name in ("H0", "H1"):
            for key in ("passed", "killed_total", "unresolved"):
                self.assertIn(f"{live['outcomes'][name][key]:.1f}%", text, (name, key))
        entry = next(e for e in self.registry if e["id"] == "tilt-v1")
        claim = next(i for i in entry["scoring"] if i["role"] == "claim")
        self.assertEqual((claim["asset"], claim["minus"], claim["horizon_weeks"]), ("NASDAQ100", "SP500", 13))

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

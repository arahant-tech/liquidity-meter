"""Provisional model board: rules as written in the study protocol, no look-ahead, no partial or private output."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from lm import catalog
from lm.measure import models
from lm.store import history

ROOT = Path(__file__).resolve().parents[1]
LAST = date(2026, 11, 11)                      # a Wednesday
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
WEEKS = 80


def wednesdays(n=WEEKS, last=LAST):
    return [last - timedelta(weeks=n - 1 - i) for i in range(n)]


def make_series(**override):
    """A quiet world in which only the last week moves, so each rule's input is easy to see."""
    days = wednesdays()
    first, final = days[0] - timedelta(days=30), LAST + timedelta(days=1)       # daily data end on Thursday
    span = [(first + timedelta(days=i)) for i in range((final - first).days + 1)]
    week = lambda f: [(d.isoformat(), repr(float(f(i)))) for i, d in enumerate(days)]
    day = lambda f: [(d.isoformat(), repr(float(f(d)))) for d in span if d.weekday() < 5]
    recent = lambda d: d > LAST - timedelta(weeks=6)
    series = {
        "L": week(lambda i: 6_000_000),                                       # total flat: s = 0
        "R": week(lambda i: 3_000_000 + (60_000 if i == WEEKS - 1 else 0)),   # reserves +1% of total
        "TGA": week(lambda i: 800_000 - (90_000 if i == WEEKS - 1 else 0)),   # more than all of it from the TGA
        "RRP": week(lambda i: 100_000),
        "BAA": day(lambda d: 1.5 if recent(d) else 1.8),                      # spread narrower
        "REAL": day(lambda d: 2.5 if recent(d) else 2.0),                     # real yield higher
        "USD": day(lambda d: 124.0 if recent(d) else 120.0),                  # dollar stronger
        "VIX": day(lambda d: 23.4567 if recent(d) else 15.0),                 # above its past median
        "FFR": day(lambda d: 4.0 if recent(d) else 3.75),                     # a hike inside 26 weeks
        "M2": [((date(2024, 1, 1) + timedelta(days=31 * i)).replace(day=1).isoformat(), repr(21_000.0 + 40 * i))
               for i in range(34)],                                           # rising
    }
    series.update(override)
    return series


class Store:
    def __init__(self, test):
        temp = tempfile.TemporaryDirectory(); test.addCleanup(temp.cleanup)
        self.root, self.runtime = Path(temp.name) / "history", Path(temp.name) / "private"
        self.output = Path(temp.name) / "public" / "models.json"

    def write(self, series, skip=()):
        index = {"schema_version": 1, "series": {}}
        for names, base in ((models.PUBLIC, self.root), (models.PRIVATE, self.runtime)):
            (base / "series").mkdir(parents=True, exist_ok=True)
            for key, sid in names.items():
                path = base / "series" / f"{sid}.csv"
                if key in skip:
                    path.unlink(missing_ok=True)
                    continue
                path.write_bytes(history.encode(sorted(series[key])))
                index["series"][sid] = {"last_date": max(d for d, _ in series[key]), "retrieved_at": "2026-11-12T22:00:00Z", "status": "ok"}
        (self.root / "index.json").write_text(json.dumps(index))

    def build(self, now=NOW):
        return models.build(self.root, self.runtime, clock=lambda: now, previous_output=self.output)


class RuleTests(unittest.TestCase):
    def last(self, **override):
        return models.weekly_table(make_series(**override))[-1]

    def test_every_rule_follows_the_protocol_on_a_hand_built_week(self):
        row = self.last()
        self.assertEqual(row["date"], LAST.isoformat())
        self.assertAlmostEqual(row["values"]["s"], 0.0)
        self.assertAlmostEqual(row["values"]["g"], 1.0)
        self.assertAlmostEqual(row["values"]["v"], 1.0)
        self.assertAlmostEqual(row["values"]["g_ex_tga"], -0.5)
        self.assertAlmostEqual(row["values"]["d_net"], 1.5)            # total − TGA − RRP rose by the TGA drawdown
        expected = {"Q1": True, "Q2": True, "Q3": False, "Q4": True, "Q5": True, "Q6": True, "Q7": False, "Q8": True,
                    "F1": True, "F2": False, "F3": False, "F4": False, "F5": False, "F6": True,
                    "C1": False, "C3": True}
        for key, state in expected.items():
            self.assertIs(row["on"][key], state, key)
        self.assertEqual(row["count"], 3)
        self.assertAlmostEqual(row["on"]["C2"], 3 / 7)

    def test_zero_counts_as_not_up_and_the_avoided_cell_is_total_up_reserves_down(self):
        flat = self.last(R=[(d.isoformat(), "3000000.0") for d in wednesdays()])
        self.assertFalse(flat["on"]["Q1"] or flat["on"]["Q2"] or flat["on"]["Q3"])
        self.assertTrue(flat["on"]["Q5"])
        grow = lambda i: 6_000_000 + (60_000 if i == WEEKS - 1 else 0)
        parked = self.last(L=[(d.isoformat(), repr(float(grow(i)))) for i, d in enumerate(wednesdays())],
                           R=[(d.isoformat(), "3000000.0") for d in wednesdays()])
        self.assertTrue(parked["on"]["Q3"])
        self.assertFalse(parked["on"]["Q5"])                           # total up, reserves not up

    def test_daily_inputs_are_read_as_of_thursday_never_later(self):
        base = make_series()
        friday = (LAST + timedelta(days=2)).isoformat()
        later = {**base, "REAL": base["REAL"] + [(friday, "0.5")], "USD": base["USD"] + [(friday, "90.0")]}
        self.assertEqual(models.weekly_table(later)[-1]["on"], models.weekly_table(base)[-1]["on"])
        thursday = (LAST + timedelta(days=1)).isoformat()
        moved = {**base, "REAL": [(d, "0.5" if d == thursday else v) for d, v in base["REAL"]]}
        self.assertTrue(models.weekly_table(moved)[-1]["on"]["F2"])    # Thursday's own value does count

    def test_volatility_is_compared_with_earlier_weeks_only(self):
        base = make_series()
        thursday = (LAST + timedelta(days=1)).isoformat()
        calm = {**base, "VIX": [(d, "10.0" if d == thursday else v) for d, v in base["VIX"]]}
        self.assertTrue(models.weekly_table(calm)[-1]["on"]["F4"])     # one low reading against 52 earlier weeks

    def test_money_supply_waits_for_its_publication_lag(self):
        base = make_series()
        # A collapse in the newest month. Its month ends less than 35 days before the decision, so it is not used yet.
        months = base["M2"][:-1] + [(base["M2"][-1][0], "1.0")]
        self.assertEqual(base["M2"][-1][0], "2026-10-01")
        self.assertTrue(models.weekly_table({**base, "M2": months})[-1]["on"]["F6"])
        older = base["M2"][:-2] + [(base["M2"][-2][0], "1.0"), base["M2"][-1]]
        self.assertFalse(models.weekly_table({**base, "M2": older})[-1]["on"]["F6"])

    def test_a_week_with_any_missing_input_is_left_out_not_filled(self):
        base = make_series()
        gap = [(d, v) for d, v in base["TGA"] if d != LAST.isoformat()]
        rows = models.weekly_table({**base, "TGA": gap})
        self.assertLess(rows[-1]["date"], LAST.isoformat())
        missing_value = [(d, None if d == LAST.isoformat() else v) for d, v in base["R"]]
        self.assertLess(models.weekly_table({**base, "R": missing_value})[-1]["date"], LAST.isoformat())


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(self)

    def test_board_reports_states_score_and_how_long_each_has_held(self):
        self.store.write(make_series())
        out = self.store.build()
        self.assertEqual((out["status"], out["asof"], out["decision_date"]), ("ok", "2026-11-11", "2026-11-13"))
        self.assertEqual(out["score"], {"on": 3, "of": 7, "voters": list(models.VOTERS)})
        self.assertEqual(set(out["states"]), set(models.RULES))
        self.assertEqual(out["states"]["Q1"], {"on": True, "since": "2026-11-11"})     # switched on this week
        # Spreads narrowed from Thursday 2026-10-01, which is the reading for the week of Wednesday 09-30.
        self.assertEqual(out["states"]["F1"]["since"], "2026-09-30")
        self.assertEqual(out["states"]["F6"]["on"], True)
        self.assertEqual(out["history"][-1], {"d": "2026-11-11", "n": 3})
        self.assertTrue(out["daily_complete"])
        self.assertEqual(out["names"], models.NAMES)
        json.dumps(out, allow_nan=False)

    def test_copyrighted_inputs_are_read_privately_and_their_values_never_published(self):
        self.store.write(make_series())
        self.assertFalse((self.store.root / "series" / "VIXCLS.csv").exists())
        self.assertFalse((self.store.root / "series" / "BAA10Y.csv").exists())
        text = json.dumps(self.store.build())
        self.assertNotIn("23.4567", text)
        self.assertEqual(set(self.store.build()["values"]), set(models.VALUES))
        self.assertFalse({"d_baa", "vix", "vix_median"} & set(models.VALUES))

    def test_a_missing_input_gives_no_partial_board(self):
        self.store.write(make_series(), skip=("VIX",))
        out = self.store.build()
        self.assertEqual((out["status"], out["reason"], out["states"]), ("unavailable", "input_missing_VIXCLS", {}))

    def test_a_failed_run_keeps_the_last_good_board_with_its_own_date(self):
        self.store.write(make_series())
        good = self.store.build()
        self.store.output.parent.mkdir(parents=True)
        self.store.output.write_text(json.dumps(good))
        self.store.write(make_series(), skip=("REAL",))
        later = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        out = self.store.build(later)
        self.assertEqual((out["status"], out["reason"], out["asof"]), ("retained", "input_missing_DFII10", "2026-11-11"))
        self.assertEqual(out["generated_at"], good["generated_at"])
        self.assertNotEqual(out["checked_at"], good["checked_at"])

    def test_old_boards_are_stale_and_future_weeks_are_refused(self):
        self.store.write(make_series())
        self.assertEqual(self.store.build(datetime(2026, 12, 20, tzinfo=timezone.utc))["status"], "stale")
        self.assertEqual(self.store.build(datetime(2026, 11, 1, tzinfo=timezone.utc))["reason"], "future_observation_rejected")

    def test_daily_values_not_yet_in_are_flagged(self):
        series = make_series()
        thursday = (LAST + timedelta(days=1)).isoformat()
        series["REAL"] = [(d, v) for d, v in series["REAL"] if d < thursday]
        self.store.write(series)
        self.assertFalse(self.store.build()["daily_complete"])


class RegistryAndStudyTests(unittest.TestCase):
    def test_board_inputs_are_registered_with_the_right_persistence(self):
        registered = {s["series_id"]: s for s in catalog.history()["series"]}
        for sid in models.PUBLIC.values():
            self.assertTrue(registered[sid]["persist"], sid)
        for sid in models.PRIVATE.values():
            self.assertFalse(registered[sid]["persist"], sid)
            self.assertTrue(registered[sid]["reason"])

    def test_names_voters_and_rule_list_match_the_fixed_study(self):
        study = json.loads((ROOT / "research/studies" / models.STUDY / "results.json").read_text())
        self.assertEqual(list(models.RULES), study["fixed"])
        self.assertEqual(list(models.VOTERS), study["voters"])
        for key in models.RULES:
            self.assertEqual(models.NAMES[key], study["models"][key])

    def test_board_reproduces_the_states_the_study_recorded_for_its_last_week(self):
        study = json.loads((ROOT / "research/studies" / models.STUDY / "results.json").read_text())
        board = json.loads((ROOT / "tests/fixtures/public/models.json").read_text())
        self.assertEqual(board["asof"], study["now"]["week"])
        for key in models.RULES:
            self.assertAlmostEqual(float(board["states"][key]["on"]), study["now"]["states"][key]["on"], places=2, msg=key)
            self.assertEqual(board["states"][key]["since"], study["now"]["states"][key]["since"], key)
        for key in models.VALUES:
            self.assertAlmostEqual(board["values"][key], study["now"]["values"][key], places=3, msg=key)

    def test_fixed_study_files_carry_no_price_levels_or_copyrighted_values(self):
        for name in ("results.json", "diagnostics.json"):
            text = (ROOT / "research/studies" / models.STUDY / name).read_text()
            for forbidden in ('"close"', '"adjclose"', '"price"', '"vix"', '"vix_median"', '"d_baa"'):
                self.assertNotIn(forbidden, text, (name, forbidden))


if __name__ == "__main__":
    unittest.main()

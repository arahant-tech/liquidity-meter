"""Scalar × vector map: exact accounting, six cells, past-only buckets, no partial or stale-as-fresh output."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from lm import catalog
from lm.measure import quadrant
from lm.store import history

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
LAST = date(2026, 11, 11)                      # a Wednesday


def weeks(n, last=LAST):
    return [(last - timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def make_series(n=220, total=lambda i: 6_000_000 + 4_000 * i, reserves=lambda i: 3_000_000 + 1_500 * ((i * 7) % 23 - 11),
                rrp=lambda i: 100_000, frrp=lambda i: 300_000, tga=lambda i: 800_000, cur=lambda i: 2_400_000):
    days = weeks(n)
    make = lambda f: {d: float(f(i)) for i, d in enumerate(days)}
    return {"L": make(total), "R": make(reserves), "RRP": make(rrp), "FRRP": make(frrp), "TGA": make(tga), "CUR": make(cur)}


class Store:
    def __init__(self, test):
        temp = tempfile.TemporaryDirectory(); test.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "history"; (self.root / "series").mkdir(parents=True)
        self.output = Path(temp.name) / "public" / "quadrant.json"

    def write(self, series, skip=(), watch=None):
        index = {"schema_version": 1, "series": {}}
        for sid, rows in (watch or {}).items():
            (self.root / "series" / f"{sid}.csv").write_bytes(history.encode(sorted(rows)))
        for key, sid in quadrant.SERIES.items():
            if key in skip:
                continue
            rows = sorted((d, repr(v)) for d, v in series[key].items())
            (self.root / "series" / f"{sid}.csv").write_bytes(history.encode(rows))
            index["series"][sid] = {"last_date": rows[-1][0], "retrieved_at": "2026-11-12T22:00:00Z", "status": "ok"}
        (self.root / "index.json").write_text(json.dumps(index))

    def build(self, now=NOW):
        return quadrant.build(self.root, clock=lambda: now, previous_output=self.output)


class AccountingTests(unittest.TestCase):
    def test_scalar_plus_vector_is_exactly_the_reserve_change(self):
        rows = quadrant.weekly_rows(make_series())
        self.assertEqual(len(rows), 220 - 13)
        for row in rows:
            self.assertAlmostEqual(row["s"] + row["v"], row["g"], places=9)
            self.assertAlmostEqual(sum(row["sources"].values()), row["v"], places=9)

    def test_values_are_scaled_by_total_assets_thirteen_weeks_earlier(self):
        series = make_series(n=14, total=lambda i: 5_000_000 + (100_000 if i == 13 else 0),
                             reserves=lambda i: 2_000_000 + (150_000 if i == 13 else 0))
        row = quadrant.weekly_rows(series)[0]
        self.assertAlmostEqual(row["s"], 2.0)             # +100B on 5,000B
        self.assertAlmostEqual(row["g"], 3.0)             # +150B on 5,000B
        self.assertAlmostEqual(row["v"], 1.0)             # 50B came out of parked balances
        self.assertEqual(row["cell"], "A")
        self.assertAlmostEqual(row["levels"]["L"], 5100.0)

    def test_a_week_without_an_exact_thirteen_week_lookback_is_left_off(self):
        series = make_series(n=40)
        missing = weeks(40)[20]
        del series["TGA"][missing]
        dates = {r["date"] for r in quadrant.weekly_rows(series)}
        self.assertNotIn(missing, dates)                                   # the week itself
        self.assertNotIn(weeks(40)[33], dates)                             # and the week that looks back to it
        self.assertEqual(len(dates), 40 - 13 - 2)

    def test_six_cells_and_their_boundaries(self):
        cases = {(2, 1): "A", (3, -1): "B+", (1, -3): "B-", (-1, 3): "C+", (-3, 1): "C-", (-1, -1): "D",
                 (0, 1): "C+", (1, 0): "B+", (0, 0): "D", (2, -2): "B-", (-2, 2): "C-"}
        for (s, v), cell in cases.items():
            with self.subTest(s=s, v=v):
                self.assertEqual(quadrant.classify(s, v), cell)
        for s, v in cases:
            side_up = quadrant.classify(s, v) in ("A", "B+", "C+")
            self.assertEqual(side_up, s + v > 0)                           # the diagonal is the reserve line


class BucketTests(unittest.TestCase):
    def test_bucket_uses_only_earlier_weeks(self):
        base = make_series(n=260)
        rows = quadrant.assign_buckets(quadrant.weekly_rows(base))
        self.assertTrue(all(r["bucket"] is None for r in rows[:quadrant.MIN_HISTORY]))
        self.assertTrue(all(r["bucket"] in (1, 2, 3, 4, 5) for r in rows[quadrant.MIN_HISTORY:]))
        changed = make_series(n=260, reserves=lambda i: 3_000_000 + 1_500 * ((i * 7) % 23 - 11) + (900_000 if i >= 250 else 0))
        again = quadrant.assign_buckets(quadrant.weekly_rows(changed))
        cut = 250 - 13                                                     # first row that can see the change
        self.assertEqual([r["bucket"] for r in rows[:cut]], [r["bucket"] for r in again[:cut]])
        self.assertNotEqual([r["g"] for r in rows[cut:]], [r["g"] for r in again[cut:]])

    def test_quantile_interpolates_linearly(self):
        self.assertAlmostEqual(quadrant.quantile([0, 10, 20, 30, 40], .2), 8.0)
        self.assertAlmostEqual(quadrant.quantile([1, 2], .8), 1.8)
        self.assertEqual(quadrant.quantile([7], .4), 7)

    def test_value_equal_to_a_boundary_stays_in_the_lower_bucket(self):
        rows = [{"g": float(x)} for x in range(quadrant.MIN_HISTORY)] + [{"g": 0.0}]
        last = quadrant.assign_buckets(rows)[-1]
        self.assertEqual(last["bucket"], 1 + sum(b < 0.0 for b in last["bounds"]))


SURGE = 215                                    # reserves jump from this week on (of 220)
flat = lambda i: 6_000_000
# Reserves wobble, dip for the 13 weeks before the surge (so those weeks sit in a low bucket), then jump.
quiet = lambda i: 3_000_000 + 1_500 * ((i * 7) % 23 - 11) - (30_000 if SURGE - 13 <= i < SURGE else 0)
surge = lambda i: quiet(i) + (900_000 if i >= SURGE else 0)


class JudgmentTests(unittest.TestCase):
    def judged(self, **series):
        rows = quadrant.assign_buckets(quadrant.weekly_rows(make_series(**series)))
        quadrant.assign_buckets(rows, "x", "xbucket", "xbounds")
        return rows, quadrant.judgment(rows)

    def test_tilt_is_growth_only_in_the_top_two_buckets(self):
        self.assertEqual([quadrant.tilt(b) for b in (None, 1, 2, 3, 4, 5)],
                         [None, "neutral", "neutral", "neutral", "growth", "growth"])

    def test_candidate_removes_exactly_the_treasury_account(self):
        flat = make_series(n=14, total=lambda i: 6_000_000, reserves=lambda i: 3_000_000 + (60_000 if i == 13 else 0),
                           tga=lambda i: 800_000 - (60_000 if i == 13 else 0))
        row = quadrant.weekly_rows(flat)[0]
        self.assertAlmostEqual(row["g"], 1.0)             # reserves +60B on 6,000B
        self.assertAlmostEqual(row["x"], 0.0)             # all of it came out of the Treasury account
        other = make_series(n=14, total=lambda i: 6_000_000, reserves=lambda i: 3_000_000 + (60_000 if i == 13 else 0))
        self.assertAlmostEqual(quadrant.weekly_rows(other)[0]["x"], 1.0)

    def test_a_surge_in_reserves_tilts_toward_growth_from_the_week_it_starts(self):
        rows, (judged, watch) = self.judged(total=flat, reserves=surge)
        self.assertEqual((judged["tilt"], judged["indicator"]["bucket"]), ("growth", 5))
        self.assertLess(rows[-6]["bucket"], 4)                        # the week before the jump did not tilt
        self.assertEqual(judged["since"], weeks(220)[SURGE])
        self.assertEqual(judged["indicator"]["tilt_from"], judged["indicator"]["bounds"][2])
        self.assertEqual(judged["indicator"]["value"], round(rows[-1]["g"], 4))
        self.assertEqual((judged["candidate"]["tilt"], judged["agree"]), ("growth", True))
        self.assertEqual(judged["source"]["driver"], "direction")     # total assets did not move
        self.assertEqual(judged["protocol"], "tilt-v1")
        self.assertFalse(watch["total_assets"]["above"])

    def test_a_surge_made_by_the_treasury_account_splits_the_two_indicators(self):
        _, (judged, _) = self.judged(total=flat, reserves=surge, tga=lambda i: 1_800_000 - (1_000_000 if i >= SURGE else 0))
        self.assertEqual((judged["indicator"]["tilt"], judged["candidate"]["tilt"], judged["agree"]),
                         ("growth", "neutral", False))
        self.assertEqual(judged["tilt"], "growth")                    # the judgment follows the main indicator only

    def test_source_names_what_made_reserves_rise(self):
        cases = {"direction": dict(total=flat, reserves=surge),
                 "total": dict(total=lambda i: 6_000_000 + (2_000_000 if i >= SURGE else 0), reserves=surge),
                 "both": dict(total=lambda i: 6_000_000 + (300_000 if i >= SURGE else 0), reserves=surge),
                 "none": dict(total=flat, reserves=lambda i: 3_000_000 - (9_000 if i >= SURGE else 0))}
        for driver, series in cases.items():
            with self.subTest(driver=driver):
                self.assertEqual(self.judged(**series)[1][0]["source"]["driver"], driver)

    def test_watch_lines(self):
        _, (_, watch) = self.judged(total=lambda i: 6_000_000 + (90_000 if i >= SURGE else 0))
        self.assertEqual((watch["total_assets"]["above"], watch["total_assets"]["since"]), (True, weeks(220)[SURGE]))
        self.assertTrue(watch["reverse_repo"]["above"])               # 100B on about 6,000B is over 1%
        _, (_, watch) = self.judged(rrp=lambda i: 200 if i < SURGE else 70_000)
        repo = watch["reverse_repo"]
        self.assertGreater(repo["share"], 1.0)                        # the latest week alone is above the line
        self.assertEqual((repo["above"], repo["since"]), (True, weeks(220)[SURGE + 3]))   # the 4-week mean crossed later
        self.assertEqual(repo["mean_weeks"], 4)

    def test_flags_are_taken_from_the_rounded_numbers_that_are_published(self):
        # +1.00004% rounds to exactly the line: the published value is 1.0 and the flag must say "not above".
        _, (_, watch) = self.judged(total=lambda i: 6_000_000 + (60_002.4 if i >= SURGE else 0))
        self.assertEqual((watch["total_assets"]["value"], watch["total_assets"]["above"]), (1.0, False))

    def test_without_enough_history_there_is_no_judgment(self):
        rows = quadrant.assign_buckets(quadrant.weekly_rows(make_series(n=60)))
        quadrant.assign_buckets(rows, "x", "xbucket", "xbounds")
        judged, _ = quadrant.judgment(rows)
        self.assertEqual((judged["tilt"], judged["agree"], judged["indicator"]["bounds"]), (None, None, None))


def business_days(last, n, value):
    days, day = [], last
    while len(days) < n:
        if day.weekday() < 5:
            days.append((day.isoformat(), repr(float(value(day)))))
        day -= timedelta(days=1)
    return days


class WatchTests(unittest.TestCase):
    def rows(self, **series):
        return quadrant.weekly_rows(make_series(n=30, total=flat, **series))

    def test_adequacy_is_reserves_over_bank_assets_as_of_the_latest_h8_week(self):
        assets = [((LAST - timedelta(weeks=1)).isoformat(), "26000.0"), ((LAST - timedelta(weeks=2)).isoformat(), "20000.0")]
        row = quadrant.attach_watch(self.rows(reserves=lambda i: 3_000_000), assets=assets)[-1]
        self.assertEqual((row["adequacy"], row["assets_date"]), (11.54, (LAST - timedelta(weeks=1)).isoformat()))   # 3,000B / 26,000B
        newer = assets + [((LAST + timedelta(days=7)).isoformat(), "1.0")]                      # a later week is never used
        self.assertEqual(quadrant.attach_watch(self.rows(reserves=lambda i: 3_000_000), assets=newer)[-1]["adequacy"], 11.54)

    def test_stale_or_missing_bank_assets_give_no_number(self):
        old = [((LAST - timedelta(days=quadrant.ASSETS_MAX_AGE_DAYS + 1)).isoformat(), "26000.0")]
        row = quadrant.attach_watch(self.rows(), assets=old)[-1]
        self.assertEqual((row["adequacy"], row["adequacy_reason"]), (None, "bank_assets_stale"))
        row = quadrant.attach_watch(self.rows())[-1]
        self.assertEqual((row["adequacy"], row["adequacy_reason"]), (None, "input_missing_TLAACBW027NBOG"))
        self.assertEqual(row["spread_reason"], "input_missing_SOFR")

    def test_spread_is_a_five_day_mean_ending_on_the_reference_wednesday(self):
        sofr = business_days(LAST + timedelta(days=2), 9, lambda d: 9.0 if d > LAST else 4.05)   # later days must not count
        iorb = business_days(LAST + timedelta(days=2), 9, lambda d: 4.0)
        row = quadrant.attach_watch(self.rows(), sofr=sofr, iorb=iorb)[-1]
        self.assertEqual((row["spread"], row["spread_through"]), (5.0, LAST.isoformat()))        # +5 basis points
        short = [r for r in sofr if r[0] > (LAST - timedelta(days=5)).isoformat()]
        row = quadrant.attach_watch(self.rows(), sofr=short, iorb=iorb)[-1]
        self.assertEqual((row["spread"], row["spread_reason"]), (None, "funding_rates_incomplete"))
        self.assertEqual(quadrant.attach_watch(self.rows(), sofr=sofr)[-1]["spread_reason"], "input_missing_IORB")

    def test_watch_block_reports_levels_lines_and_how_long_they_have_held(self):
        rows = quadrant.assign_buckets(self.rows(reserves=lambda i: 3_000_000 if i < 27 else 3_300_000))
        quadrant.assign_buckets(rows, "x", "xbucket", "xbounds")
        assets = [(d, "26000.0") for d in weeks(30)]
        sofr = business_days(LAST, 40, lambda d: 4.10 if d > LAST - timedelta(days=6) else 3.95)   # high from Friday on
        iorb = business_days(LAST, 40, lambda d: 4.0)
        quadrant.attach_watch(rows, assets, sofr, iorb)
        _, watch = quadrant.judgment(rows)
        adequacy, spread = watch["reserve_adequacy"], watch["funding_spread"]
        self.assertEqual((adequacy["value"], adequacy["below"], adequacy["line"], adequacy["band_high"]), (12.69, False, 12.0, 13.0))
        self.assertEqual(adequacy["since"], weeks(30)[27])                                      # 11.54% before the jump
        self.assertEqual((spread["value_bp"], spread["above"], spread["days"], spread["through"]), (7.0, True, 5, LAST.isoformat()))
        self.assertEqual(spread["since"], LAST.isoformat())                                     # a week earlier the mean was still below zero
        _, watch = quadrant.judgment(quadrant.attach_watch(rows))
        self.assertEqual(watch["reserve_adequacy"], {"value": None, "reason": "input_missing_TLAACBW027NBOG"})
        self.assertEqual(watch["funding_spread"], {"value_bp": None, "reason": "input_missing_SOFR"})


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(self)

    def test_public_map_shape_and_no_price_series(self):
        self.store.write(make_series())
        out = self.store.build()
        self.assertEqual((out["status"], out["asof"]), ("ok", LAST.isoformat()))
        self.assertEqual(set(out["cells"]), set(quadrant.CELLS))
        self.assertAlmostEqual(sum(c["share"] for c in out["cells"].values()), 1.0, places=2)
        self.assertEqual(out["now"]["cell"], out["points"][-1]["c"])
        self.assertEqual(set(out["points"][0]), {"d", "s", "v", "c", "b", "x", "xb"})
        self.assertEqual(set(out["inputs"]), set(quadrant.SERIES.values()) | set(quadrant.WATCH_SERIES.values()))
        self.assertEqual(out["inputs"]["WALCL"]["retrieved_at"], "2026-11-12T22:00:00Z")
        text = json.dumps(out)
        for forbidden in ("SP500", "NASDAQCOM", "CBBTCUSD", "return", "forecast"):
            self.assertNotIn(forbidden, text)

    def test_map_carries_the_judgment_and_the_watch_items(self):
        self.store.write(make_series(total=flat, reserves=surge))
        out = self.store.build()
        self.assertEqual(out["judgment"]["tilt"], "growth")
        self.assertEqual(out["judgment"]["indicator"]["bucket"], out["now"]["bucket"])
        self.assertEqual(out["judgment"]["indicator"]["value"], out["now"]["g"])
        self.assertEqual(set(out["watch"]), {"total_assets", "reverse_repo", "reserve_adequacy", "funding_spread"})
        last = out["points"][-1]
        self.assertEqual((last["b"], last["xb"]), (out["judgment"]["indicator"]["bucket"], out["judgment"]["candidate"]["bucket"]))
        json.dumps(out, allow_nan=False)

    def test_inputs_are_registered_persisted_histories(self):
        registered = {s["series_id"]: s for s in catalog.history()["series"]}
        for sid in list(quadrant.SERIES.values()) + list(quadrant.WATCH_SERIES.values()):
            self.assertTrue(registered[sid]["persist"], sid)

    def test_a_missing_watch_input_does_not_cost_the_map(self):
        self.store.write(make_series(), watch={"SOFR": business_days(LAST, 10, lambda d: 4.0)})
        out = self.store.build()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["watch"]["reserve_adequacy"], {"value": None, "reason": "input_missing_TLAACBW027NBOG"})
        self.assertEqual(out["watch"]["funding_spread"], {"value_bp": None, "reason": "input_missing_IORB"})
        self.assertEqual(set(out["inputs"]), set(quadrant.SERIES.values()) | set(quadrant.WATCH_SERIES.values()))

    def test_old_latest_week_is_marked_stale_not_fresh(self):
        self.store.write(make_series())
        out = self.store.build(now=NOW + timedelta(days=20))
        self.assertEqual(out["status"], "stale")
        self.assertEqual(out["asof"], LAST.isoformat())

    def test_missing_input_keeps_the_last_good_map_marked_retained(self):
        self.store.write(make_series())
        good = self.store.build()
        self.store.output.parent.mkdir(parents=True)
        self.store.output.write_text(json.dumps(good))
        (self.store.root / "series" / "WDTGAL.csv").unlink()
        later = NOW + timedelta(days=7)
        out = self.store.build(now=later)
        self.assertEqual((out["status"], out["reason"]), ("retained", "input_missing_WDTGAL"))
        self.assertEqual(out["asof"], good["asof"])                        # the original date, not today's
        self.assertEqual(out["generated_at"], good["generated_at"])
        self.assertEqual(out["checked_at"], "2026-11-20T12:00:00Z")
        self.assertEqual(out["points"], good["points"])

    def test_missing_input_without_a_previous_map_is_unavailable_not_partial(self):
        self.store.write(make_series(), skip=("RRP",))
        out = self.store.build()
        self.assertEqual((out["status"], out["reason"]), ("unavailable", "input_missing_WLRRAOL"))
        self.assertEqual(out["points"], [])
        self.assertIsNone(out["now"])

    def test_future_dated_observation_is_rejected(self):
        self.store.write(make_series())
        out = self.store.build(now=NOW - timedelta(days=30))
        self.assertEqual((out["status"], out["reason"]), ("unavailable", "future_observation_rejected"))

    def test_command_writes_the_map_and_reports_failure_by_exit_code(self):
        import contextlib, io
        self.store.write(make_series(), skip=("CUR",))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = quadrant.main(["--history", str(self.store.root), "--output", str(self.store.output)])
        self.assertEqual(code, 2)
        written = json.loads(self.store.output.read_text())
        self.assertEqual(written["status"], "unavailable")
        self.assertEqual(json.loads(printed.getvalue())["reason"], "input_missing_WCURCIR")


if __name__ == "__main__":
    unittest.main()

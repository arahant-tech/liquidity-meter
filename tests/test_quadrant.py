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

    def write(self, series, skip=()):
        index = {"schema_version": 1, "series": {}}
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
        self.assertEqual(set(out["points"][0]), {"d", "s", "v", "c", "b"})
        self.assertEqual(set(out["inputs"]), set(quadrant.SERIES.values()))
        self.assertEqual(out["inputs"]["WALCL"]["retrieved_at"], "2026-11-12T22:00:00Z")
        text = json.dumps(out)
        for forbidden in ("SP500", "NASDAQCOM", "CBBTCUSD", "return", "forecast"):
            self.assertNotIn(forbidden, text)

    def test_inputs_are_registered_persisted_histories(self):
        registered = {s["series_id"]: s for s in catalog.history()["series"]}
        for sid in quadrant.SERIES.values():
            self.assertTrue(registered[sid]["persist"], sid)

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

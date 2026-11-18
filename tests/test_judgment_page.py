"""Judgment screen: the page refuses a map that is malformed, inconsistent or silently old."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from lm.measure import models, quadrant

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
NOW = "Date.parse('2026-11-17T12:00:00Z')"


class JudgmentPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node runtime unavailable")
        # CI checks a frozen real map; the pipeline points LM_PUBLIC_DIR at the one it is about to publish.
        live = os.environ.get("LM_PUBLIC_DIR")
        cls.live = Path(live).resolve() / "quadrant.json" if live else None
        cls.fixture = ROOT / "tests" / "fixtures" / "public" / "quadrant.json"
        cls.live_board = Path(live).resolve() / "models.json" if live else None
        cls.board = ROOT / "tests" / "fixtures" / "public" / "models.json"
        cls.live_record = Path(live).resolve() / "ledger.json" if live else None
        cls.record = ROOT / "tests" / "fixtures" / "public" / "ledger.json"

    def evaluate(self, body, path=None, second=None):
        setup = "const j=require(process.argv[1]);const fs=require('fs');const q=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
        run = subprocess.run([self.node, "-e", setup + body, str(SITE / "judgment.js"), str(path or self.fixture), str(second or "")],
                             capture_output=True, text=True, check=True)
        return json.loads(run.stdout)

    def test_page_and_measurement_classify_identically(self):
        grid = [(s / 2, v / 2) for s in range(-6, 7) for v in range(-6, 7)]
        got = self.evaluate("console.log(JSON.stringify(%s.map(([s,v])=>j.classify(s,v))));" % json.dumps(grid))
        self.assertEqual(got, [quadrant.classify(s, v) for s, v in grid])
        self.assertEqual(set(self.evaluate("console.log(JSON.stringify(j.CELLS));")), set(quadrant.CELLS))

    def test_real_map_passes_the_page_contract(self):
        out = self.evaluate(f"const r=j.validate(q,{NOW});console.log(JSON.stringify([r.status,r.points.length,r.now.cell,r.age]));")
        self.assertEqual(out[0], "ok")
        self.assertGreater(out[1], 900)
        self.assertIn(out[2], quadrant.CELLS)

    def test_map_about_to_be_published_passes_the_page_contract(self):
        if not self.live:
            self.skipTest("only runs in the pipeline, against the map it is about to publish")
        if not self.live.exists():
            self.skipTest("no map was produced in this run; the page shows that state itself")
        out = self.evaluate("const r=j.validate(q);console.log(JSON.stringify(r.status));", self.live)
        self.assertIn(out, ("ok", "stale", "retained", "unavailable"))

    def test_an_ok_map_that_is_old_is_shown_as_stale(self):
        out = self.evaluate("const r=j.validate(q,Date.parse('2026-12-20T00:00:00Z'));console.log(JSON.stringify([r.status,r.age>14]));")
        self.assertEqual(out, ["stale", True])

    def test_retained_and_unavailable_states_survive_validation(self):
        out = self.evaluate(f"q.status='retained';q.reason='input_missing_WDTGAL';const a=j.validate(q,{NOW});"
                            f"const b=j.validate({{schema_version:1,status:'unavailable',reason:'input_missing_WALCL',points:[]}},{NOW});"
                            "console.log(JSON.stringify([a.status,a.points.length>0,b.status,b.reason,b.points.length]));")
        self.assertEqual(out, ["retained", True, "unavailable", "input_missing_WALCL", 0])

    def test_broken_maps_are_refused(self):
        cases = {
            "wrong schema": "q.schema_version=2;",
            "unknown status": "q.status='fine';",
            "no points": "q.points=[];",
            "dates out of order": "[q.points[5],q.points[6]]=[q.points[6],q.points[5]];",
            "duplicate date": "q.points[6].d=q.points[5].d;",
            "non-numeric value": "q.points[9].s='1.2';",
            "unknown cell": "q.points[9].c='E';",
            "cell contradicts its values": "q.points[9]={...q.points[9],s:3,v:2,c:'D'};",
            "bad bucket": "q.points[q.points.length-1].b=7;",
            "as-of date differs from last point": "q.asof='2026-01-07';",
            "current cell differs from last point": "q.now.cell=q.now.cell==='A'?'D':'A';",
            "future week": "const p=q.points[q.points.length-1];p.d='2027-03-03';q.asof=p.d;q.now.date=p.d;",
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                out = self.evaluate(mutate + f"try{{j.validate(q,{NOW});console.log('false')}}catch{{console.log('true')}}")
                self.assertTrue(out, label)

    def test_real_board_passes_the_page_contract_and_agrees_with_the_map(self):
        out = self.evaluate(f"const m=j.validateModels(q,{NOW});const map=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
                            "console.log(JSON.stringify([m.status,m.score.on,m.score.of,j.boardMatchesMap(m,map),j.RULES,j.ASSETS]));",
                            self.board, self.fixture)
        self.assertEqual(out[0], "ok")
        self.assertEqual(out[2], len(models.VOTERS))
        self.assertTrue(out[3])
        self.assertEqual(out[4], list(models.RULES))
        study = json.loads((ROOT / "research/studies" / models.STUDY / "results.json").read_text())
        self.assertEqual(out[5], list(study["assets"]))

    def test_board_about_to_be_published_passes_the_page_contract(self):
        if not self.live_board:
            self.skipTest("only runs in the pipeline, against the board it is about to publish")
        if not self.live_board.exists():
            self.skipTest("no board was produced in this run; the page shows that state itself")
        out = self.evaluate("const m=j.validateModels(q);console.log(JSON.stringify(m.status));", self.live_board)
        self.assertIn(out, ("ok", "stale", "retained", "unavailable"))

    def test_board_states_survive_and_old_boards_are_stale(self):
        out = self.evaluate(f"const a=j.validateModels(q,Date.parse('2026-12-20T00:00:00Z'));q.status='retained';q.reason='input_missing_VIXCLS';"
                            f"const b=j.validateModels(q,{NOW});const c=j.validateModels({{schema_version:1,status:'unavailable',reason:'input_missing_M2SL'}},{NOW});"
                            "console.log(JSON.stringify([a.status,b.status,c.status,c.reason]));", self.board)
        self.assertEqual(out, ["stale", "retained", "unavailable", "input_missing_M2SL"])

    def test_broken_boards_are_refused(self):
        cases = {
            "wrong schema": "q.schema_version=2;",
            "unknown status": "q.status='fine';",
            "missing rule": "delete q.states.F3;",
            "missing name": "delete q.names.Q8;",
            "non-boolean state": "q.states.Q1.on='yes';",
            "weight out of range": "q.states.C2.on=1.4;",
            "score differs from its states": "q.score.on+=1;",
            "weight differs from the score": "q.states.C2.on=q.states.C2.on>0.5?0.1:0.9;",
            "majority differs from the score": "q.states.C1.on=!q.states.C1.on;",
            "voter outside the rule list": "q.score.voters[0]='Z9';",
            "since after the as-of week": "q.states.Q2.since='2027-01-06';",
            "history out of order": "[q.history[3],q.history[4]]=[q.history[4],q.history[3]];",
            "history count out of range": "q.history[2].n=9;",
            "history ends on another week": "q.history.pop();",
            "empty history": "q.history=[];",
            "future week": "q.asof='2027-03-03';q.history[q.history.length-1].d='2027-03-03';",
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                out = self.evaluate(mutate + f"try{{j.validateModels(q,{NOW});console.log('false')}}catch{{console.log('true')}}", self.board)
                self.assertTrue(out, label)

    def test_board_that_contradicts_the_map_for_the_same_week_is_not_shown(self):
        body = ("const map=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));const m=j.validateModels(q,%s);"
                "const flip=JSON.parse(JSON.stringify(m));flip.states.Q2.on=!flip.states.Q2.on;"
                "const other=JSON.parse(JSON.stringify(flip));other.asof='2026-11-04';"
                "console.log(JSON.stringify([j.boardMatchesMap(m,map),j.boardMatchesMap(flip,map),j.boardMatchesMap(other,map)]));" % NOW)
        self.assertEqual(self.evaluate(body, self.board, self.fixture), [True, False, True])

    def test_model_study_has_what_the_board_reads(self):
        base = ROOT / "research/studies" / models.STUDY
        study, diag = json.loads((base / "results.json").read_text()), json.loads((base / "diagnostics.json").read_text())
        self.assertRegex(study["asof"], r"^\d{4}-\d{2}-\d{2}$")
        for asset, block in study["assets"].items():
            for key in models.RULES:
                row = block["models"][key]
                self.assertTrue({"spread", "p_raw", "p_global", "grade", "periods", "delayed"} <= set(row), (asset, key))
                self.assertIn(row["grade"], ("통과", "후보", "미달"))
                # a pass must hold under both tests before the page may call it one
                self.assertIn(diag[asset]["conservative_grades"][key], ("통과", "후보", "미달"))
                if diag[asset]["conservative_grades"][key] == "통과":
                    self.assertLess(diag[asset]["block_bootstrap"][key]["p_global"], 0.10)
                    self.assertLess(row["p_global"], 0.10)
            for label, cell in diag[asset]["by_count"].items():
                self.assertRegex(label, r"^\d(~\d)?$")
                self.assertTrue({"weeks", "mean"} <= set(cell))
        html = (SITE / "index.html").read_text()
        for target in ("./models.json", f"./research/studies/{models.STUDY}/README.md"):
            self.assertIn(f'href="{target}"', html)
        manifest = json.loads((ROOT / "lm/render/public-files.json").read_text())["repo"]
        for name in ("PROTOCOL.md", "README.md", "results.json", "diagnostics.json"):
            self.assertIn(f"research/studies/{models.STUDY}/{name}", manifest)

    def test_real_map_carries_a_judgment_that_follows_the_measurement_rule(self):
        out = self.evaluate(f"const r=j.validate(q,{NOW});console.log(JSON.stringify([r.judgment,r.watch,Object.keys(j.TILTS)]));")
        judged, watch, tilts = out
        self.assertEqual(set(tilts), {"growth", "neutral"})
        for reading in (judged["indicator"], judged["candidate"]):
            self.assertEqual(reading["tilt"], quadrant.tilt(reading["bucket"]))
            self.assertEqual(reading["tilt_from"], reading["bounds"][quadrant.TILT_FROM_BUCKET - 2])
        self.assertEqual(judged["protocol"], quadrant.PROTOCOL)
        self.assertEqual((watch["total_assets"]["line"], watch["reverse_repo"]["line"]), (quadrant.TOTAL_LINE, quadrant.RRP_SHARE_LINE))
        self.assertEqual((watch["reserve_adequacy"]["line"], watch["reserve_adequacy"]["band_high"]),
                         (quadrant.ADEQUACY_LINE, quadrant.ADEQUACY_BAND_HIGH))
        self.assertEqual((watch["funding_spread"]["line_bp"], watch["funding_spread"]["days"]),
                         (quadrant.SPREAD_LINE_BP, quadrant.SPREAD_DAYS))

    def test_watch_items_without_their_inputs_or_from_an_earlier_map_still_draw(self):
        out = self.evaluate("q.watch.reserve_adequacy={value:null,reason:'input_missing_TLAACBW027NBOG'};"
                            "q.watch.funding_spread={value_bp:null,reason:'input_missing_SOFR'};"
                            f"const a=j.validate(q,{NOW});const empty=a.watch.reserve_adequacy.value;"
                            f"delete q.watch.reserve_adequacy;delete q.watch.funding_spread;const b=j.validate(q,{NOW});"
                            "console.log(JSON.stringify([a.status,empty,b.status,Object.keys(b.watch).length]));")
        self.assertEqual(out, ["ok", None, "ok", 2])

    def test_an_older_map_without_a_judgment_still_draws(self):
        out = self.evaluate(f"delete q.judgment;delete q.watch;const r=j.validate(q,{NOW});console.log(JSON.stringify([r.status,r.judgment===undefined]));")
        self.assertEqual(out, ["ok", True])

    def test_judgments_that_contradict_themselves_or_the_map_are_refused(self):
        cases = {
            "tilt contradicts its bucket": "q.judgment.indicator.tilt='growth';q.judgment.tilt='growth';",
            "bucket differs from the map": "q.judgment.indicator.bucket=2;",
            "value differs from the map": "q.judgment.indicator.value+=0.5;",
            "bucket contradicts its bounds": "q.judgment.candidate.value=9;",
            "bounds out of order": "q.judgment.indicator.bounds=[1,0,2,3];",
            "tilt line is not the fourth bucket's edge": "q.judgment.indicator.tilt_from=0;",
            "headline differs from the indicator": "q.judgment.tilt=q.judgment.tilt==='growth'?'neutral':'growth';",
            "wrong comparison": "q.judgment.agree=!q.judgment.agree;",
            "unknown source": "q.judgment.source.driver='magic';",
            "candidate bucket out of range": "q.judgment.candidate.bucket=9;",
            "watch flag contradicts its value": "q.watch.total_assets.above=!q.watch.total_assets.above;",
            "repo flag contradicts its mean": "q.watch.reverse_repo.above=!q.watch.reverse_repo.above;",
            "judgment without watch items": "delete q.watch;",
            "adequacy flag contradicts its value": "q.watch.reserve_adequacy.below=!q.watch.reserve_adequacy.below;",
            "adequacy without a reason for being empty": "q.watch.reserve_adequacy={value:null};",
            "spread flag contradicts its value": "q.watch.funding_spread.above=!q.watch.funding_spread.above;",
            "spread with an unreadable value": "q.watch.funding_spread.value_bp='3';",
            "bad candidate bucket on a point": "q.points[400].xb=0;",
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                out = self.evaluate(mutate + f"try{{j.validate(q,{NOW});console.log('false')}}catch{{console.log('true')}}")
                self.assertTrue(out, label)

    def test_tilt_study_and_protocol_have_what_the_judgment_block_reads(self):
        base = ROOT / "research/studies/tilt-2026-11"
        study, diag = json.loads((base / "results.json").read_text()), json.loads((base / "diagnostics.json").read_text())
        for key in ("mean_on", "mean_off", "D", "p_raw", "p_holm", "grade"):
            self.assertIn(key, study["indicators"]["g"])
        self.assertIn("D", study["indicators"]["x"])
        self.assertTrue({"from", "to", "weeks"} <= set(study["sample"]))
        self.assertTrue({"total_up_mean", "total_not_up_mean", "total_not_up_weeks"} <= set(diag["inside_on_weeks"]))
        text = json.dumps(study) + json.dumps(diag)
        for forbidden in ('"close"', '"adjclose"', '"price"'):
            self.assertNotIn(forbidden, text)
        controls = json.loads((ROOT / "research/studies/tilt-controls-2026-11/results.json").read_text())
        self.assertTrue({"b", "p_holm", "retention"} <= set(controls["primary"]["tests"]["T_AB"]))
        self.assertIn(controls["verdicts"]["종합"], ("남는다", "일부 설명된다", "설명된다"))
        # the control study must start from exactly the tilt study's number
        self.assertAlmostEqual(controls["primary"]["b0"], study["indicators"]["g"]["D"], places=2)
        self.assertEqual(controls["sample"]["weeks"], study["sample"]["weeks"])
        for forbidden in ('"close"', '"adjclose"', '"price"', '"vix"'):
            self.assertNotIn(forbidden, json.dumps(controls))
        registry = json.loads((ROOT / "research/protocols/registry.json").read_text())["protocols"]
        self.assertIn(quadrant.PROTOCOL, [entry["id"] for entry in registry])
        html = (SITE / "index.html").read_text()
        for target in ("./research/protocols/PROTOCOL_tilt_v1.md", "./research/studies/tilt-2026-11/README.md", "./ledger.json"):
            self.assertIn(f'href="{target}"', html)
        self.assertIn("지수 방향에 대한 판단이 아니다", html)
        manifest = json.loads((ROOT / "lm/render/public-files.json").read_text())["repo"]
        for name in ("research/protocols/PROTOCOL_tilt_v1.md", "research/protocols/registry.json",
                     "research/studies/tilt-2026-11/results.json", "research/studies/tilt-2026-11/diagnostics.json",
                     "research/studies/tilt-2026-11/README.md", "research/studies/tilt-2026-11/PROTOCOL.md",
                     "research/studies/tilt-controls-2026-11/results.json", "research/studies/tilt-controls-2026-11/README.md",
                     "research/studies/tilt-controls-2026-11/PROTOCOL.md"):
            self.assertIn(name, manifest)

    def test_record_summary_is_read_as_counts_only(self):
        out = self.evaluate(f"console.log(JSON.stringify(j.ledgerSummary(q,'{quadrant.PROTOCOL}')));", self.record)
        self.assertEqual((out["sealed"], out["scored"], out["horizon"], out["nextReview"]), (1, 0, 13, 104))
        self.assertEqual((out["first"], out["last"], out["on"], out["off"], out["difference"]), ("2026-11-11", "2026-11-11", 0, 1, None))
        scored = self.evaluate(
            f"const p=q.protocols['{quadrant.PROTOCOL}'];p.confirmed=120;p.on_weeks=40;p.off_weeks=80;p.last_week='2029-02-21';"
            "p.assets[0].scored=107;p.assets[0].on_off={on:{n:36,mean:0.031},off:{n:71,mean:0.004},difference:0.027};"
            f"console.log(JSON.stringify(j.ledgerSummary(q,'{quadrant.PROTOCOL}')));", self.record)
        self.assertEqual((scored["scored"], scored["nextReview"]), (107, 156))
        self.assertAlmostEqual(scored["difference"], 2.7)                                       # shown in percentage points

    def test_unreadable_record_summaries_give_nothing_rather_than_a_guess(self):
        cases = {
            "another protocol": f"delete q.protocols['{quadrant.PROTOCOL}'];",
            "wrong schema": "q.schema_version=2;",
            "negative count": f"q.protocols['{quadrant.PROTOCOL}'].confirmed=-1;",
            "weeks out of order": f"q.protocols['{quadrant.PROTOCOL}'].first_week='2027-01-06';",
            "more scored than sealed": f"q.protocols['{quadrant.PROTOCOL}'].assets[0].scored=5;",
            "no claim": f"q.protocols['{quadrant.PROTOCOL}'].assets.forEach((a)=>a.role='reference');",
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                out = self.evaluate(mutate + f"console.log(JSON.stringify(j.ledgerSummary(q,'{quadrant.PROTOCOL}')));", self.record)
                self.assertIsNone(out, label)

    def test_record_summary_about_to_be_published_is_readable(self):
        if not self.live_record:
            self.skipTest("only runs in the pipeline, against the summary it is about to publish")
        if not self.live_record.exists():
            self.skipTest("no summary yet; the page says the first seal is awaited")
        out = self.evaluate(f"console.log(JSON.stringify(j.ledgerSummary(q,'{quadrant.PROTOCOL}')));", self.live_record)
        self.assertIsNotNone(out)

    def test_entry_page_loads_the_map_and_links_the_other_screens(self):
        html = (SITE / "index.html").read_text()
        self.assertRegex(html, r'<script\b[^>]*\bsrc="\./judgment\.js"')
        self.assertRegex(html, r'<link\b[^>]*\bhref="\./judgment\.css"')
        for target in ("./observatory.html", "./sources.html", "./quadrant.json", "./research/studies/quadrant-2026-11/README.md"):
            self.assertIn(f'href="{target}"', html)
        self.assertIn("투자 조언이 아닙니다", html)
        self.assertIn("FRED® API", html)
        self.assertNotIn("const DATA", html)                       # nothing is baked into the page
        self.assertNotRegex(html, r"20\d\d-\d\d-\d\d")            # no dated numbers either

    def test_old_overview_moved_and_still_wired(self):
        html = (SITE / "observatory.html").read_text()
        self.assertIn("overview.js", html)
        self.assertIn("'./','판단 화면 ↗'", (SITE / "overview.js").read_text())
        self.assertIn('href="./observatory.html"', (SITE / "sources.html").read_text())

    def test_shared_design_tokens_match_the_rest_of_the_site(self):
        def tokens(name):
            block = re.search(r":root\{([^}]*)\}", (SITE / name).read_text().replace("\n", "")).group(1)
            return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+)", block))
        base, mine = tokens("dashboard.css"), tokens("judgment.css")
        shared = set(base) & set(mine)
        self.assertGreater(len(shared), 12)
        for name in sorted(shared):
            self.assertEqual(mine[name].strip(), base[name].strip(), name)

    def test_study_table_has_what_the_page_reads_and_no_price_levels(self):
        study = json.loads((ROOT / "research/studies/quadrant-2026-11/results.json").read_text())
        for key in list(quadrant.CELLS) + ["g>0", "g<=0", "all"]:
            for asset in ("SPY", "QQQ", "QQQ−SPY", "BTC", "ETH"):
                row = study["cell_returns"][key][asset]
                self.assertTrue({"n", "fwd13", "hit"} <= set(row), (key, asset))
        text = json.dumps(study)
        for forbidden in ('"close"', '"adjclose"', '"price"', '"points"'):
            self.assertNotIn(forbidden, text)
        self.assertRegex(study["asof"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()

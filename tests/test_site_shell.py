"""One site: the same header and footer everywhere, links that lead somewhere, documents readable inside it."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES = {"index.html": "판정", "measure.html": "측정", "models.html": "모델", "observatory.html": "관측",
         "sources.html": "자료", "research.html": "연구·검증", "doc.html": "연구·검증"}
DATA_FILES = {"live-data.json", "chart-data.json", "quadrant.json", "models.json", "ledger.json"}
DOC_PATH = re.compile(r"^(research|docs)/[A-Za-z0-9_\-./]+\.md$")


def manifest():
    return json.loads((ROOT / "lm/render/public-files.json").read_text())


def published_documents():
    docs = {p for p in manifest()["repo"] if p.endswith(".md")}
    docs |= {"docs/" + p.name for p in (ROOT / "docs").glob("*_SOURCE*.md")} | {"docs/CLOUD_OPERATION.md"}
    return docs


class ShellTests(unittest.TestCase):
    def block(self, html, tag, cls):
        return re.search(rf'<{tag} class="{cls}">.*?</{tag}>', html, flags=re.S).group(0)

    def test_every_page_carries_the_same_header_and_footer(self):
        headers, footers = set(), set()
        for name, current in PAGES.items():
            html = (SITE / name).read_text()
            with self.subTest(page=name):
                header = self.block(html, "header", "topbar")
                marked = re.findall(r'<a href="[^"]*" aria-current="page">([^<]*)</a>', header)
                self.assertEqual(marked, [current])                      # exactly one door is lit, the right one
                headers.add(header.replace(' aria-current="page"', ""))
                footers.add(self.block(html, "footer", "footer"))
                self.assertIn("<strong>유동성 측정기</strong>", header)
                self.assertNotIn("유동성 관측실", html)
                self.assertRegex(html, r"<title>[^<]+ · 유동성 측정기</title>")
                self.assertRegex(html, r'<link rel="stylesheet" href="\./site\.css">')
                self.assertLess(html.index('href="./site.css"'), html.index("</head>"))
                first_sheet = re.search(r'<link rel="stylesheet" href="\./([a-z.]+)"', html).group(1)
                self.assertEqual(first_sheet, "site.css")              # page styles come after and add, not replace
        self.assertEqual(len(headers), 1)
        self.assertEqual(len(footers), 1)
        footer = footers.pop()
        self.assertIn("투자 조언이 아닙니다", footer)
        self.assertIn("FRED® API", footer)

    def test_the_six_doors_are_the_six_pages(self):
        header = self.block((SITE / "index.html").read_text(), "header", "topbar")
        doors = re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', header.split("<nav", 1)[1])
        self.assertEqual([label for _, label in doors], ["판정", "측정", "모델", "관측", "자료", "연구·검증"])
        for href, _ in doors:
            self.assertTrue(href == "./" or (SITE / href[2:]).is_file(), href)

    def test_page_stylesheets_leave_the_shell_alone(self):
        shell = re.compile(r"(^|[,}\s])(\.topbar|\.brand|\.toplinks|\.shell|\.hero|\.footer|\.eyebrow|\.run-panel|\.badge|body|html|:root)[\s,{.:]")
        for name in ("content.css", "dashboard.css", "overview.css"):
            text = re.sub(r"/\*.*?\*/", "", (SITE / name).read_text(), flags=re.S)
            selectors = [s for s in re.findall(r"([^{}]+)\{", text) if not s.strip().startswith("@")]
            offenders = [s.strip() for s in selectors if shell.search(" " + s.strip() + " ")]
            self.assertEqual(offenders, [], name)

    def test_every_local_link_leads_to_something_published(self):
        files = set(manifest()["site"]) | DATA_FILES
        docs = published_documents()
        repo = set(manifest()["repo"])
        for name in list(PAGES) + ["judgment.js", "overview.js", "research.js"]:
            text = (SITE / name).read_text()
            links = re.findall(r'''(?:href|src)=["'](\./[^"'#]*)''', text) + re.findall(r"""['"](\./[A-Za-z0-9_./?=-]+\.(?:html|json|md)(?:\?p=[A-Za-z0-9_./-]+)?)['"]""", text)
            for link in set(links):
                with self.subTest(source=name, link=link):
                    target = link[2:]
                    if not target:
                        continue                                       # "./" is the home page
                    if target.startswith("doc.html?p="):
                        path = target.split("=", 1)[1]
                        self.assertRegex(path, DOC_PATH)
                        self.assertIn(path, docs)
                        self.assertTrue((ROOT / path).is_file())
                    elif "?" not in target and not target.endswith("/"):
                        self.assertTrue(target in files or target in repo, target)
        for name in PAGES:
            html = (SITE / name).read_text()
            self.assertNotRegex(html, r'href="(?!\./doc\.html\?p=)[^"]*\.md"')   # documents open in the viewer, not as raw text
        self.assertNotIn(".md'", (SITE / "overview.js").read_text().replace("doc.html?p=docs/OVERVIEW_SOURCE.md'", ""))


class ResearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = json.loads((ROOT / "research/index.json").read_text())

    def test_every_listed_file_exists_and_is_published(self):
        repo = set(manifest()["repo"])
        self.assertIn("research/index.json", repo)
        paths = [a["path"] for a in self.index["archive"]]
        for study in self.index["studies"]:
            paths += [study["readme"], study["protocol"], *study["data"]]
            for key in ("id", "date", "title", "grade", "question", "finding"):
                self.assertTrue(study[key], (study.get("id"), key))
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertIn(path, repo)

    def test_every_study_in_the_repository_is_listed(self):
        on_disk = {p.parent.name for p in (ROOT / "research/studies").glob("*/README.md")}
        self.assertEqual({s["id"] for s in self.index["studies"]}, on_disk)
        for study in self.index["studies"]:
            self.assertEqual(study["readme"], f"research/studies/{study['id']}/README.md")

    def test_protocols_carry_what_the_page_shows(self):
        registry = json.loads((ROOT / "research/protocols/registry.json").read_text())["protocols"]
        for entry in registry:
            self.assertTrue(entry["title"] and entry["claim"], entry["id"])
            self.assertIn("research/protocols/" + entry["document"], manifest()["repo"])


class DocumentViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node runtime unavailable")

    def run_js(self, body, *args):
        run = subprocess.run([self.node, "-e", "const d=require(process.argv[1]);" + body, str(SITE / "doc.js"), *map(str, args)],
                             capture_output=True, text=True, check=True)
        return json.loads(run.stdout)

    def test_only_the_projects_own_documents_can_be_opened(self):
        cases = ["research/studies/tilt-2026-11/README.md", "docs/OVERVIEW_SOURCE.md", "../secrets.md", "research/../../x.md",
                 "https://example.com/x.md", "research/x.html", "/etc/passwd", "research//x.md", "research/./x.md", "site/doc.js", ""]
        out = self.run_js("console.log(JSON.stringify(JSON.parse(process.argv[2]).map(d.safePath)));", json.dumps(cases))
        self.assertEqual(out, [cases[0], cases[1]] + [None] * 9)

    def test_markdown_is_rendered_and_text_is_never_trusted(self):
        source = "\n".join([
            "# 제목", "", "문단 **굵게** 와 `코드 <b>` 그리고 <script>alert(1)</script>.", "",
            "- 하나", "- 둘", "  - 둘의 아래", "", "1. 첫째", "2. 둘째", "",
            "| 이름 | 값 |", "|---|---:|", "| a | +1.90 |", "",
            "> 인용", "", "```sh", "python x.py <arg>", "```", "", "---", "",
            "[다른 문서](PROTOCOL.md) [밖](https://example.com/a) [위험](javascript:alert(1)) [자료](results.json) [스크립트](scripts/study.py) [위로](../../../x.md)",
        ])
        html = self.run_js("console.log(JSON.stringify(d.render(process.argv[2],'research/studies/tilt-2026-11/README.md')));", source)
        self.assertIn('<h1 id="제목">제목</h1>', html)
        self.assertIn("<strong>굵게</strong>", html)
        self.assertIn("<code>코드 &lt;b&gt;</code>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("<ul><li>하나</li><li>둘<ul><li>둘의 아래</li></ul></li></ul>", html)
        self.assertIn("<ol><li>첫째</li><li>둘째</li></ol>", html)
        self.assertIn('<td style="text-align:right">+1.90</td>', html)
        self.assertIn("<blockquote><p>인용</p></blockquote>", html)
        self.assertIn("<pre><code>python x.py &lt;arg&gt;</code></pre>", html)
        self.assertIn("<hr>", html)
        self.assertIn('<a href="./doc.html?p=research/studies/tilt-2026-11/PROTOCOL.md">다른 문서</a>', html)
        self.assertIn('<a href="https://example.com/a" target="_blank" rel="noopener noreferrer">밖 ↗</a>', html)
        self.assertNotIn("javascript:", html)
        self.assertIn('<a href="./research/studies/tilt-2026-11/results.json">자료 ↗</a>', html)
        self.assertNotIn("study.py\"", html)                               # unpublished files stay plain text
        self.assertNotIn("x.md", html.split("위험")[1].split("자료")[0])

    def test_every_published_document_renders_and_its_links_stay_inside_the_site(self):
        docs = sorted(published_documents())
        program = ("const fs=require('fs');const out={};for(const p of JSON.parse(process.argv[2])){"
                   "const html=d.render(fs.readFileSync(process.argv[3]+'/'+p,'utf8'),p);"
                   "out[p]={size:html.length,links:[...html.matchAll(/href=\"\\.\\/doc\\.html\\?p=([^\"#]+)/g)].map(m=>m[1]),"
                   "files:[...html.matchAll(/href=\"\\.\\/((?:research|docs)\\/[^\"]+)\"/g)].map(m=>m[1]),"
                   "raw:/<script|onerror=|javascript:/i.test(html)}}console.log(JSON.stringify(out));")
        out = self.run_js(program, json.dumps(docs), ROOT)
        repo = set(manifest()["repo"])
        self.assertGreater(len(out), 15)
        for path, result in out.items():
            with self.subTest(document=path):
                self.assertGreater(result["size"], 100)
                self.assertFalse(result["raw"])
                for link in result["links"]:
                    self.assertIn(link, docs)                          # a link to a document that is not published would be dead
                for link in result["files"]:
                    # a data file the repository has must be published; one it never had is unlinked by the viewer's check
                    self.assertTrue(link in repo or not (ROOT / link).exists(), link)

    def test_viewer_page_escapes_before_it_inserts(self):
        script = (SITE / "doc.js").read_text()
        self.assertEqual(script.count("innerHTML"), 1)                    # one insertion point, fed only by render()
        self.assertIn("body.innerHTML = render(text, path)", script)
        self.assertIn("safePath(new URLSearchParams(location.search).get('p'))", script)
        self.assertIn("method: 'HEAD'", script)                           # links to files the site lacks are checked, not left dead
        self.assertIn("a.replaceWith(plain)", script)


if __name__ == "__main__":
    unittest.main()

"""This repository is public. Nothing private may be committed to it."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "data", ".run", "_site", "__pycache__", ".venv-cloud", ".venv-ci"}
PATTERNS = {
    "personal home path": re.compile(r"/(?:Users|home)/(?!private\b|user\b|runner\b)[A-Za-z][\w.-]*/"),
    "private artifact link": re.compile(r"claude\.ai/(?:code/)?artifact/[0-9a-f-]{8,}"),
    "FRED key in a URL or assignment": re.compile(r"(?:api_key=|FRED_API_KEY\s*[=:]\s*['\"]?)[A-Za-z0-9]{32}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private key block": re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    "launchd job label": re.compile(r"\bcom\.[a-z]+\.liqdesk\b"),
}
# Synthetic secrets that exist only to prove redaction works.
ALLOWED = {("tests/test_live_api.py", "FRED key in a URL or assignment"),
           ("tests/test_hygiene.py", "private key block")}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split("\n")
        files = [ROOT / name for name in out if name]
    except Exception:
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in files if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]


class HygieneTests(unittest.TestCase):
    def test_no_private_paths_links_or_credentials(self):
        findings = []
        for path in tracked_files():
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            relative = path.relative_to(ROOT).as_posix()
            for label, pattern in PATTERNS.items():
                if (relative, label) in ALLOWED:
                    continue
                match = pattern.search(text)
                if match:
                    findings.append(f"{relative}: {label}: {match.group(0)[:40]}")
        self.assertEqual(findings, [])

    def test_mac_only_operating_files_are_not_imported(self):
        names = {p.name for p in tracked_files()}
        self.assertFalse({n for n in names if n.endswith((".plist", ".command"))})
        self.assertNotIn("publish_pages.sh", names)
        self.assertNotIn("run_weekly.sh", names)

    def test_state_and_build_output_are_ignored_on_main(self):
        ignored = (ROOT / ".gitignore").read_text().split("\n")
        for entry in ("/data/", ".run/", "_site/", ".env"):
            self.assertIn(entry, ignored)


if __name__ == "__main__":
    unittest.main()

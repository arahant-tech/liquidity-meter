"""Small behavior regressions for published feed presentation, without a browser dependency."""
import json
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def page_root():
    return Path(__file__).resolve().parents[1] / 'site'


class CloudPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The overview is now the entry page; existing raw-feed behavior remains
        # on the explicit details page rather than being silently skipped.
        cls.page = page_root() / 'sources.html'
        node = shutil.which('node')
        bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        if not node and bundled.exists():
            node = str(bundled)
        if not node:
            raise unittest.SkipTest('Node.js is required for the static page behavior check')
        cls.html = cls.page.read_text()
        for name in ('site.css', 'dashboard.css'):        # status marks live in the shared stylesheet
            css = cls.page.with_name(name)
            if css.exists():
                cls.html += '\n' + css.read_text()
        script = cls.html.split('<script>')[1].split('</script>')[0]
        prefix = script.split("document.querySelectorAll('.filter').forEach(button")[0]
        program = "const vm=require('vm');const input=JSON.parse(require('fs').readFileSync(0,'utf8'));new vm.Script(input.script);console.log(vm.runInNewContext(input.prefix+input.query,{document:{createElement:()=>({})},URL,Intl,Date,Number,JSON,Array,Object,String}));"
        query = "JSON.stringify({retained:badge('retained'),missing:numeric(null),zero:numeric(0),tiny:numeric(1e-10),scriptUrl:safeUrl('javascript:alert(1)'),dataUrl:safeUrl('data:text/html,x'),issuer:providerKey('issuer_buybacks'),sec:providerKey('buybacks')})"
        result = subprocess.run([node, '-e', program], input=json.dumps({'script': script, 'prefix': prefix, 'query': query}), text=True, capture_output=True, check=True)
        cls.result = json.loads(result.stdout)

    def test_retained_value_is_explicit_and_amber(self):
        self.assertEqual(self.result['retained']['textContent'], '이전 정상값')
        self.assertEqual(self.result['retained']['className'], 'badge retained')
        self.assertIn('.badge.partial,.badge.stale,.badge.retained{', self.html)

    def test_missing_and_small_values_are_not_displayed_as_zero(self):
        self.assertEqual(self.result['missing'], '—')
        self.assertEqual(self.result['zero'], '0')
        self.assertNotEqual(self.result['tiny'], '0')

    def test_unsafe_source_links_and_distinct_buyback_providers(self):
        self.assertIsNone(self.result['scriptUrl'])
        self.assertIsNone(self.result['dataUrl'])
        self.assertEqual(self.result['issuer'], 'issuer_buybacks')
        self.assertEqual(self.result['sec'], 'buybacks')


class CloudPackagingTests(unittest.TestCase):
    """The packager publishes an explicit allow-list from site/, the data snapshot and named reports."""
    SITE = ('index.html', 'sources.html', 'overview.js', 'overview.css', 'interpretation.js',
            'interpretation-copy.json', 'vendor/chart.umd.min.js', 'charts.js', 'dashboard.css', '.nojekyll')

    @classmethod
    def setUpClass(cls):
        from lm.render import package as packager
        from lm import catalog
        cls.packager = packager
        cls.providers = tuple(p['id'] for p in catalog.providers())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest({'site': list(self.SITE), 'repo': ['research/archive/study/REPORT.md']})
        for name in self.SITE:
            self.write('site/' + name, 'public fixture: ' + name)
        for name in ('research/archive/study/REPORT.md', 'docs/CLOUD_OPERATION.md', 'docs/SYNTHETIC_SOURCE.md'):
            self.write(name, 'public fixture: ' + name)
        self.write('data/public/live-data.json', json.dumps({'research_eligible': False, 'generated_at': '2026-09-09T00:00:00Z',
                                                             'providers': [{'provider': p} for p in self.providers]}))
        self.write('data/public/chart-data.json', json.dumps({'research_eligible': False, 'vintage_policy': 'current_snapshot_not_historical_availability'}))

    def manifest(self, value):
        self.write('lm/render/public-files.json', json.dumps(value))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def package(self):
        with patch.object(self.packager, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            self.packager.package()

    def published(self):
        return {str(p.relative_to(self.root / '_site')) for p in (self.root / '_site').rglob('*') if p.is_file()}

    def test_entry_sources_and_all_local_dependencies_are_explicitly_packaged(self):
        self.package()
        names = self.published()
        required = set(self.SITE) | {'chart-data.json', 'live-data.json', 'docs/CLOUD_OPERATION.md',
                                     'docs/SYNTHETIC_SOURCE.md', 'research/archive/study/REPORT.md'}
        self.assertEqual(names, required)
        for name in self.SITE:
            self.assertEqual((self.root / '_site' / name).read_bytes(), (self.root / 'site' / name).read_bytes())
        for name in ('chart-data.json', 'live-data.json'):
            self.assertEqual((self.root / '_site' / name).read_bytes(), (self.root / 'data/public' / name).read_bytes())

    def test_private_runtime_code_and_unapproved_files_are_not_swept_in(self):
        excluded = ('data/live/raw/receipt.json', 'data/ledger/records.jsonl', 'lm/collect/live_api.py', '.env',
                    '.run/raw.json', 'site/private-preview.json', 'docs/PRIVATE_NOTES.md',
                    'research/archive/study/unlisted.json', 'research/protocols/DRAFT.md')
        for name in excluded:
            self.write(name, 'PRIVATE_CREDENTIAL_SENTINEL')
        self.package()
        for name in excluded:
            self.assertFalse((self.root / '_site' / name).exists())
        self.assertNotIn('private-preview.json', self.published())
        for path in (self.root / '_site').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE_CREDENTIAL_SENTINEL', path.read_bytes())

    def test_missing_vendor_file_fails_instead_of_publishing_a_broken_overview(self):
        (self.root / 'site/vendor/chart.umd.min.js').unlink()
        with self.assertRaisesRegex(ValueError, 'unsafe_or_missing_public_artifact'):
            self.package()

    def test_symlinked_public_asset_is_rejected(self):
        (self.root / 'site/overview.js').unlink()
        self.write('private.js', 'PRIVATE_CREDENTIAL_SENTINEL')
        (self.root / 'site/overview.js').symlink_to(self.root / 'private.js')
        with self.assertRaisesRegex(ValueError, 'unsafe_or_missing_public_artifact'):
            self.package()

    def test_manifest_cannot_escape_the_public_output_directory(self):
        for bad in ({'site': ['../outside.txt'], 'repo': []}, {'site': [], 'repo': ['../outside.txt']},
                    {'site': ['/etc/hostname'], 'repo': []}):
            with self.subTest(manifest=bad):
                self.manifest(bad)
                with self.assertRaisesRegex(ValueError, 'unsafe_public_artifact_path'):
                    self.package()

    def test_quadrant_map_is_published_only_when_it_exists(self):
        self.package()
        self.assertNotIn('quadrant.json', self.published())           # absent: the page says so itself
        self.write('data/public/quadrant.json', '{"schema_version": 1}')
        self.write('data/public/unlisted.json', 'PRIVATE_CREDENTIAL_SENTINEL')
        self.package()
        self.assertIn('quadrant.json', self.published())
        self.assertNotIn('unlisted.json', self.published())           # still an allow-list, not a sweep

    def test_symlinked_quadrant_map_is_rejected(self):
        self.write('private.json', 'PRIVATE_CREDENTIAL_SENTINEL')
        (self.root / 'data/public/quadrant.json').symlink_to(self.root / 'private.json')
        with self.assertRaisesRegex(ValueError, 'unsafe_or_missing_public_artifact'):
            self.package()

    def test_snapshot_must_cover_exactly_the_registered_providers(self):
        self.write('data/public/live-data.json', json.dumps({'research_eligible': False, 'generated_at': '2026-09-09T00:00:00Z',
                                                             'providers': [{'provider': p} for p in self.providers[:-1]]}))
        with self.assertRaisesRegex(ValueError, 'provider_status_incomplete'):
            self.package()

    def test_real_manifest_names_only_files_that_exist(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'lm/render/public-files.json').read_text())
        for relative in manifest['site']:
            self.assertTrue((root / 'site' / relative).is_file(), relative)
        for relative in manifest['repo']:
            self.assertTrue((root / relative).is_file(), relative)


if __name__ == '__main__':
    unittest.main()

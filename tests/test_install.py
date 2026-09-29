"""Installation checks use explicit temporary roots; never write to a real home."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
NPM = shutil.which('npm')


@unittest.skipUnless(NODE, 'Node.js is required for installer tests')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fleet-install-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'fresh root'

    def command(self, *args, ok=True, entry=None):
        result = subprocess.run([NODE, str(entry or REPO / 'bin/fleet.js'), *map(str, args)],
                                text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def setup(self, harness='all', **kwargs):
        return self.command('setup', '--harness', harness, '--root', self.root, **kwargs)

    def remove(self, harness='all', **kwargs):
        return self.command('uninstall', '--harness', harness, '--root', self.root, **kwargs)

    def installed(self, harness='.claude'):
        return self.root / harness / 'skills/fleet'

    def test_fresh_all_idempotent_and_uninstall(self):
        self.setup()
        for harness in ['.claude', '.agents', '.cursor']:
            target = self.installed(harness)
            self.assertTrue((target / 'SKILL.md').is_file())
            self.assertEqual((target / 'LICENSE').read_bytes(), (REPO / 'LICENSE').read_bytes())
            self.assertTrue((target / 'bin/fleet').is_file())
            self.assertFalse(target.is_symlink())
            self.assertEqual((target / 'providers.toml').read_bytes(), (REPO / 'skill/providers.toml').read_bytes())
        before = (self.installed() / '.fleet-install.json').read_bytes()
        self.setup()
        self.assertEqual(before, (self.installed() / '.fleet-install.json').read_bytes())
        self.assertFalse((self.root / '.config').exists())
        self.remove()
        self.remove()
        self.assertFalse(self.installed().exists())

    def test_unowned_conflict_preflight_prevents_partial_install(self):
        conflict = self.installed('.cursor')
        conflict.mkdir(parents=True)
        (conflict / 'SKILL.md').write_text('mine')
        self.setup(ok=False)
        self.assertFalse(self.installed().exists())
        self.assertEqual((conflict / 'SKILL.md').read_text(), 'mine')

    def test_modified_install_is_preserved_on_update_and_uninstall(self):
        self.setup('claude')
        custom = self.installed() / 'SKILL.md'
        custom.write_text('custom instruction')
        self.setup('claude', ok=False)
        self.remove('claude', ok=False)
        self.assertEqual(custom.read_text(), 'custom instruction')

    def test_extra_user_file_and_empty_directory_are_preserved(self):
        self.setup('claude')
        added = self.installed() / 'notes'
        added.mkdir()
        self.remove('claude', ok=False)
        added.rmdir()
        added.write_text('keep')
        self.remove('claude', ok=False)
        self.assertEqual(added.read_text(), 'keep')

    def test_symlink_target_is_rejected(self):
        self.root.mkdir()
        external = self.base / 'external'
        external.mkdir()
        (self.root / '.claude').symlink_to(external, target_is_directory=True)
        self.setup('claude', ok=False)
        self.assertEqual(list(external.iterdir()), [])

    def test_existing_source_checkout_link_is_not_replaced(self):
        target = self.installed()
        target.parent.mkdir(parents=True)
        target.symlink_to(REPO / 'skill', target_is_directory=True)
        self.setup('claude', ok=False)
        self.remove('claude', ok=False)
        self.assertTrue(target.is_symlink())

    def test_project_scope_and_single_harness(self):
        self.command('setup', '--harness', 'codex', '--scope', 'project', '--root', self.root)
        self.assertTrue(self.installed('.agents').exists())
        self.assertFalse(self.installed().exists())

    def test_explicit_harness_and_valid_arguments_are_required(self):
        for args in [[], ['--harness', 'bogus'], ['--harness', 'all', '--scope', 'bad'],
                     ['--harness', 'codex', '--root', 'relative'], ['--harness']]:
            self.command('setup', *args, ok=False)
        self.command('setup', '--help')
        self.assertFalse(self.root.exists())

    def test_source_shell_installer(self):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), '--harness', 'cursor', '--root', str(self.root)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.installed('.cursor').exists())

    @unittest.skipUnless(NPM, 'npm is required for tarball checks')
    def test_published_tarball_is_complete_and_copies_survive_package_removal(self):
        result = subprocess.run([NPM, 'pack', '--json', '--pack-destination', str(self.base),
                                 '--cache', str(self.base / 'npm-cache')], cwd=REPO, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        packed = json.loads(result.stdout)[0]
        tar_path = self.base / packed['filename']
        with tarfile.open(tar_path) as archive:
            names = archive.getnames()
            self.assertIn('package/LICENSE', names)
            self.assertIn('package/skill/LICENSE', names)
            self.assertIn('package/dist/codex/agent-fleet/skills/fleet/LICENSE', names)
            self.assertIn('package/.claude-plugin/plugin.json', names)
            self.assertIn('package/dist/codex/agent-fleet/.codex-plugin/plugin.json', names)
            self.assertIn('package/dist/codex/agent-fleet/skills/fleet/SKILL.md', names)
            self.assertNotIn('package/dist/codex/agent-fleet/skills/fleet/.fleet-install.json', names)
            self.assertIn('package/skill/templates/preamble.md', names)
            self.assertIn('package/skill/providers.toml', names)
            self.assertFalse(any('__pycache__' in name or '/tests/' in name or name.endswith('REVIEW.md') for name in names))
            archive.extractall(self.base, filter='data')
        for source_file in (self.base / 'package/skill').rglob('*'):
            if source_file.is_file():
                generated = self.base / 'package/dist/codex/agent-fleet/skills/fleet' / source_file.relative_to(self.base / 'package/skill')
                expected = source_file.read_bytes()
                if source_file.name == 'SKILL.md':
                    expected = expected.replace(b'disable-model-invocation: true\n', b'')
                self.assertEqual(expected, generated.read_bytes())
        npm_root = self.base / 'npm-install'
        installed = subprocess.run([NPM, 'install', '--prefix', str(npm_root), '--ignore-scripts',
                                    '--no-audit', '--no-fund', '--cache', str(self.base / 'npm-cache'), str(tar_path)],
                                   text=True, capture_output=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        entry = npm_root / 'node_modules/.bin/fleet'
        self.command('--version', entry=entry)
        self.command('setup', '--harness', 'all', '--root', self.root, entry=entry)
        shutil.rmtree(self.base / 'package')
        shutil.rmtree(npm_root)
        runtime = self.installed() / 'bin/fleet'
        import sys
        run = subprocess.run([sys.executable, str(runtime), '--help'], text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('usage:', run.stdout)
        run = subprocess.run([sys.executable, str(runtime), 'providers'], text=True, capture_output=True,
                             env={**os.environ, 'FLEET_CONFIG': str(self.base / 'absent.toml')})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('codex', run.stdout)
        self.remove()

    def test_launcher_version_and_missing_python(self):
        expected = json.loads((REPO / 'package.json').read_text())['version']
        self.assertEqual(self.command('--version').stdout.strip(), expected)
        run = subprocess.run([NODE, str(REPO / 'bin/fleet.js'), 'providers'], text=True, capture_output=True,
                             env={**os.environ, 'FLEET_PYTHON': str(self.base / 'missing-python')})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Python 3.11+', run.stderr)

    def test_plugin_metadata_matches_package(self):
        package = json.loads((REPO / 'package.json').read_text())
        for manifest_path in ['.claude-plugin/plugin.json', 'plugin-manifests/codex.json']:
            manifest = json.loads((REPO / manifest_path).read_text())
            self.assertEqual(manifest['version'], package['version'])
            skills = manifest.get('skills', './skills/')
            if isinstance(skills, str):
                skills = [skills]
            for skill_path in skills:
                self.assertIn(skill_path, ('./skills/', './skill/'))


if __name__ == '__main__':
    unittest.main()

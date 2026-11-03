import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest.mock import Mock, patch

from tools import install


class InstallEnvironment(unittest.TestCase):
    def test_creates_isolated_environment_and_reuses_it_without_losing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Crosspost test folder'
            root.mkdir()
            python = install.prepare_environment(root)
            self.assertTrue(python.is_file())
            result = subprocess.run([str(python), '-c',
                                     'import sys, site, pip; print(sys.prefix); print(site.ENABLE_USER_SITE)'],
                                    check=True, capture_output=True, text=True)
            self.assertIn(str(root / '.venv'), result.stdout)
            self.assertIn('False', result.stdout)
            marker = root / '.venv' / 'keep-installed-packages'
            marker.write_text('keep', encoding='utf-8')
            cfg = root / '.venv' / 'pyvenv.cfg'
            cfg.write_text(cfg.read_text(encoding='utf-8').replace(
                'include-system-site-packages = false', 'include-system-site-packages = true'), encoding='utf-8')
            self.assertEqual(install.prepare_environment(root), python)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')
            self.assertIn('include-system-site-packages = false', cfg.read_text(encoding='utf-8'))

    def test_creation_failure_never_installs_into_system_python(self):
        with patch.object(install, 'prepare_environment', side_effect=OSError('read-only folder')), \
             patch.object(install.subprocess, 'call') as call:
            self.assertEqual(install.main(), 1)
            call.assert_not_called()

    def test_system_interpreter_delegates_installation_to_project_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = install.environment_python(root)
            with patch.object(install, 'ROOT', root), \
                 patch.object(install, 'prepare_environment', return_value=python), \
                 patch.object(install.subprocess, 'call', return_value=7) as call:
                self.assertEqual(install.main(), 7)
                call.assert_called_once_with([str(python), str(Path(install.__file__).resolve())], cwd=root)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows batch launcher')
    def test_debug_launcher_uses_local_environment_in_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Crosspost test folder'
            root.mkdir()
            venv.EnvBuilder(with_pip=False).create(root / '.venv')
            (root / '.venv' / '.crosspost-ready').write_text('ready', encoding='utf-8')
            (root / 'Crosspost-debug.bat').write_bytes((install.ROOT / 'Crosspost-debug.bat').read_bytes())
            app = root / 'app'
            app.mkdir()
            (app / '__main__.py').write_text(
                "import sys; from pathlib import Path; Path('interpreter.txt').write_text(sys.prefix, encoding='utf-8')",
                encoding='utf-8')
            subprocess.run(['cmd', '/d', '/c', 'Crosspost-debug.bat'], cwd=root,
                           input='\n', text=True, capture_output=True, check=True, timeout=30)
            self.assertEqual(Path((root / 'interpreter.txt').read_text(encoding='utf-8')).resolve(),
                             (root / '.venv').resolve())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows batch launcher')
    def test_failed_setup_stops_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Install.bat').write_bytes(b'@echo off\r\nexit /b 23\r\n')
            for launcher in ('Crosspost.bat', 'Crosspost-debug.bat'):
                with self.subTest(launcher=launcher):
                    (root / launcher).write_bytes((install.ROOT / launcher).read_bytes())
                    result = subprocess.run(['cmd', '/d', '/c', launcher], cwd=root,
                                            input='\n', text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)

    def test_failed_dependency_install_invalidates_previous_ready_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / '.venv'
            env.mkdir()
            marker = env / '.crosspost-ready'
            marker.write_text('ready', encoding='utf-8')
            with patch.object(install, 'ROOT', root), \
                 patch.object(install, 'prepare_environment', return_value=install.environment_python(root)), \
                 patch.object(sys, 'prefix', str(env)), \
                 patch.object(install.subprocess, 'call', side_effect=[0, 1]):
                self.assertEqual(install.main(), 1)
            self.assertFalse(marker.exists())

    def test_ready_marker_requires_successful_imports_but_webview_is_optional(self):
        for missing, expected in [('PIL', 1), ('webview', 0), (None, 0)]:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = root / '.venv'
                env.mkdir()
                modules = {m: Mock() for m in ('pandas', 'numpy', 'requests', 'PIL', 'webview',
                                              'telethon', 'python_socks', 'playwright', 'pystray', 'psutil')}
                if missing:
                    modules[missing] = None
                with patch.object(install, 'ROOT', root), \
                     patch.object(install, 'prepare_environment', return_value=install.environment_python(root)), \
                     patch.object(sys, 'prefix', str(env)), \
                     patch.dict(sys.modules, modules), \
                     patch.object(install.subprocess, 'call', return_value=0):
                    self.assertEqual(install.main(), expected)
                self.assertEqual((env / '.crosspost-ready').exists(), expected == 0)

    def test_update_uses_console_python_from_same_environment_for_pip(self):
        from app import selfupdate, subproc
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / '.venv' / 'Scripts'
            scripts.mkdir(parents=True)
            console, windowed = scripts / 'python.exe', scripts / 'pythonw.exe'
            console.touch()
            windowed.touch()
            updates = root / 'updates'
            updates.mkdir()
            with patch.object(selfupdate, 'ready', return_value={'root': str(root), 'version': '0.2.12'}), \
                 patch.object(selfupdate, 'can_update', return_value=(True, '')), \
                 patch.object(selfupdate, '_check_tree'), \
                 patch.object(selfupdate, '_upd', return_value=updates), \
                 patch.object(selfupdate, 'app_dir', return_value=root), \
                 patch.object(selfupdate, 'data_dir', return_value=root), \
                 patch.object(sys, 'platform', 'win32'), \
                 patch.object(sys, 'executable', str(windowed)), \
                 patch.object(selfupdate.subprocess, 'Popen') as spawn:
                self.assertEqual(subproc.python_exe(), str(console))
                selfupdate.apply(stop_worker=False, restart=False)
            command = spawn.call_args.args[0]
            self.assertEqual(command[command.index('--python') + 1], str(console))
            self.assertEqual(command[command.index('--pythonw') + 1], str(windowed))
            self.assertIn('--no-restart', command)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows batch launcher')
    def test_existing_incomplete_environment_runs_setup_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Crosspost test folder'
            root.mkdir()
            venv.EnvBuilder(with_pip=False).create(root / '.venv')
            (root / 'Install.bat').write_bytes(
                b'@echo off\r\necho setup>setup-attempted.txt\r\nexit /b 23\r\n')
            for launcher in ('Crosspost.bat', 'Crosspost-debug.bat'):
                with self.subTest(launcher=launcher):
                    (root / launcher).write_bytes((install.ROOT / launcher).read_bytes())
                    result = subprocess.run(['cmd', '/d', '/c', launcher], cwd=root,
                                            input='\n', text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue((root / 'setup-attempted.txt').exists())
                    (root / 'setup-attempted.txt').unlink()

    def test_update_detects_changes_in_dependency_constraints(self):
        from app.apply_update import dependency_signature
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'requirements-app.txt').write_text('-c requirements-constraints.txt\nrequests>=2.31\n')
            (root / 'requirements-constraints.txt').write_text('requests==2.31.0\n')
            before = dependency_signature(root)
            (root / 'requirements-constraints.txt').write_text('requests==2.34.2\n')
            self.assertNotEqual(dependency_signature(root), before)


if __name__ == '__main__':
    unittest.main()

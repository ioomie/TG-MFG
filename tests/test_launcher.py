import json
import sys
import tempfile
import unittest
import io
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_core
import launcher


class LauncherTests(unittest.TestCase):
    def test_only_exact_start_action_is_accepted(self):
        for url in ("tg-mfg://start", "tg-mfg://start/"):
            self.assertTrue(launcher.valid_activation(url))
        for url in ("tg-mfg://start?command=calc", "tg-mfg://start#secret", "tg-mfg://start/file", "tg-mfg://stop",
                    "tg-mfg://start:123", "file:///tmp/run.sh", 'tg-mfg://start" --command', "TG-MFG://start"):
            with self.subTest(url=url), patch.object(sys, "argv", ["launcher.py", url]), patch.object(launcher, "start_core") as start:
                self.assertEqual(launcher.main(), 2)
                start.assert_not_called()

    def test_fixed_per_user_directories(self):
        self.assertEqual(install_core.installation_directory("darwin", "/home/alice", {}), Path("/home/alice/Library/Application Support/TG-MFG"))
        self.assertEqual(install_core.installation_directory("win32", "/home/alice", {"LOCALAPPDATA": "/profile/local"}), Path("/profile/local/TG-MFG"))
        with self.assertRaises(RuntimeError):
            install_core.installation_directory("linux", "/tmp", {})

    def test_copy_excludes_sessions_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "download", root / "install"
            (source / "web").mkdir(parents=True)
            (source / "app.py").write_text("test")
            (source / "web/index.html").write_text("test page")
            (source / "user.session").write_text("must not copy")
            (source / "private.key").write_text("must not copy")
            install_core.copy_core(source, target)
            self.assertTrue((target / "app.py").exists())
            self.assertTrue((target / "web/index.html").exists())
            self.assertFalse((target / "user.session").exists())
            self.assertFalse((target / "private.key").exists())

    def test_activation_uses_fixed_script_not_shell(self):
        target = Path("/profile with spaces/TG-MFG")
        command = install_core.windows_protocol_command(target)
        self.assertIn(f'"{target / "runtime" / "pythonw.exe"}"', command)
        self.assertIn(f'"{target / "launcher.py"}" "%1"', command)
        self.assertNotIn("cmd.exe", command)

    def make_core(self, root):
        executable = root / "python"
        executable.write_text("fixture")
        (root / "app.py").write_text("fixture")
        (root / "installation.json").write_text(json.dumps({"id": "test-id"}))
        return executable

    def test_running_core_is_reused_and_other_install_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self.make_core(root)
            with patch.object(launcher, "core_python", return_value=executable), patch.object(launcher, "core_state", return_value={"installation": {"id": "test-id"}}), patch.object(launcher.subprocess, "Popen") as spawn:
                launcher.start_core(root)
                spawn.assert_not_called()
            with patch.object(launcher, "core_python", return_value=executable), patch.object(launcher, "core_state", return_value={"installation": {"id": "other-id"}}):
                with self.assertRaisesRegex(RuntimeError, "Another TG-MFG core"):
                    launcher.start_core(root)
            with patch.object(launcher, "core_python", return_value=executable), patch.object(launcher, "core_state", return_value={"installation": None}):
                with self.assertRaisesRegex(RuntimeError, "Another TG-MFG core"):
                    launcher.start_core(root)

    def test_start_waits_for_its_own_core_and_uses_no_url_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self.make_core(root)
            process = Mock()
            process.poll.return_value = None
            with patch.object(launcher, "core_python", return_value=executable), patch.object(launcher, "core_state", side_effect=[None, {"installation": {"id": "test-id"}}]), patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
                launcher.start_core(root)
                self.assertEqual(spawn.call_args.args[0], [str(executable), "-u", "-B", "-X", "utf8", str(root / "app.py")])
                self.assertFalse(spawn.call_args.kwargs.get("shell", False))

    def test_missing_core_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Run the installer again"):
                launcher.start_core(Path(directory))

    def test_duplicate_launcher_clicks_are_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with launcher.launch_lock(root) as first:
                self.assertTrue(first)
                with launcher.launch_lock(root) as second:
                    self.assertFalse(second)
            with launcher.launch_lock(root) as later:
                self.assertTrue(later)

    def test_reinstall_stops_only_the_same_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "installation.json").write_text(json.dumps({"id": "owned-id"}))
            for state in ({"installation": None}, {"installation": {"id": "other-id"}}, None):
                with patch.object(install_core, "core_state", return_value=state), patch.object(install_core, "urlopen") as fetch:
                    install_core.stop_owned_core(root)
                    fetch.assert_not_called()
            state = {"token": "local-token", "installation": {"id": "owned-id"}}
            with patch.object(install_core, "core_state", side_effect=[state, None]), patch.object(install_core, "urlopen", return_value=io.BytesIO(b'{"stopped":true}')) as fetch:
                install_core.stop_owned_core(root)
                request = fetch.call_args.args[0]
                self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/shutdown")
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.data, b"{}")

    def test_install_receipt_contains_no_account_data_and_native_operations_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            (source / "runtime").mkdir(parents=True)
            (source / "runtime/pythonw.exe").write_text("fixture")
            (source / "app.py").write_text("fixture")
            (source / "core-settings.json").write_text(json.dumps({"portal_origin": "https://portal.example"}))
            with patch.object(install_core, "SOURCE", source), patch.object(sys, "platform", "win32"), patch.object(install_core, "installation_directory", return_value=target), patch.object(install_core, "stop_owned_core"), patch.object(install_core, "register_windows") as register, patch.object(install_core.subprocess, "run") as run, patch.object(install_core.webbrowser, "open") as browser:
                install_core.main()
                register.assert_called_once_with(target)
                self.assertEqual(run.call_args.args[0][-1], "tg-mfg://start")
                browser.assert_called_once_with("https://portal.example")
            receipt = json.loads((target / "installation.json").read_text())
            self.assertEqual(set(receipt), {"managed", "platform", "version", "id"})
            self.assertTrue(receipt["managed"])


if __name__ == "__main__":
    unittest.main()

"""Exercise the actual installer activation/reboot block in a temporary tree."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


BASH = shutil.which("bash")
if not BASH and os.name == "nt":
    candidate = Path("C:/Program Files/Git/usr/bin/bash.exe")
    if candidate.exists():
        BASH = str(candidate)


@unittest.skipUnless(BASH and os.name == "posix", "Installer activation checks need POSIX symlink semantics")
class InstallActivationTests(unittest.TestCase):
    def activate(self, existing, healthy):
        source = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
        activation = source[source.index("previous=''\n"):]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / "new"
            release.mkdir(parents=True)
            old = root / "releases" / "old"
            old.mkdir()
            quote = lambda p: shlex.quote(str(p).replace("\\", "/"))
            calls = root / "calls"
            setup = f"""set -euo pipefail
export PATH="/usr/bin:/bin:$PATH"
commit={'a' * 40}
install_user=pi-owner
release={quote(release)}
calls={quote(calls)}
healthy_stub={str(healthy).lower()}
systemctl() {{
  echo "$*" >> "$calls"
  if [[ $1 == is-active ]]; then [[ $healthy_stub == true ]]; fi
}}
curl() {{ echo '{{"installed_commit":"{'a' * 40}"}}'; }}
python3() {{ cat >/dev/null; [[ $healthy_stub == true ]]; }}
sleep() {{ :; }}
hostname() {{ if [[ ${{1:-}} == -I ]]; then echo 192.168.1.112; else echo test-pi; fi; }}
"""
            if existing:
                setup += f"ln -s {quote(old)} {quote(root / 'current')}\n"
            # Absolute system paths are redirected to this disposable test tree.
            script = setup + activation.replace("/opt/counter", str(root).replace("\\", "/"))
            result = subprocess.run([BASH, "-c", script], text=True, capture_output=True, timeout=20)
            self.assertTrue(calls.exists(), result.stderr)
            commands = calls.read_text().splitlines()
            current = root / "current"
            # Read the link through Bash for compatibility with Git Bash on Windows.
            link = subprocess.run([BASH, "-c", f"readlink -f {quote(current)}"], text=True, capture_output=True).stdout.strip()
            exists = subprocess.run([BASH, "-c", f"test -L {quote(current)}"], capture_output=True).returncode == 0
            return result, commands, link, exists

    def test_successful_first_install_reboots(self):
        result, commands, link, exists = self.activate(existing=False, healthy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reboot", commands)
        self.assertTrue(exists)
        self.assertTrue(link.endswith("/releases/new"))
        self.assertIn("Initial install succeeded", result.stdout)
        self.assertIn("http://192.168.1.112:8080", result.stdout)

    def test_successful_update_only_restarts_service(self):
        result, commands, link, exists = self.activate(existing=True, healthy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("reboot", commands)
        self.assertIn("restart counter.service", commands)
        self.assertTrue(link.endswith("/releases/new"))

    def test_failed_first_install_never_reboots_or_leaves_active_release(self):
        result, commands, link, exists = self.activate(existing=False, healthy=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("reboot", commands)
        self.assertIn("stop counter.service", commands)
        self.assertFalse(exists)

    def test_failed_update_restores_previous_release_without_reboot(self):
        result, commands, link, exists = self.activate(existing=True, healthy=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("reboot", commands)
        self.assertTrue(exists)
        self.assertTrue(link.endswith("/releases/old"))

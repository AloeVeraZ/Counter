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
        source = (Path(__file__).parents[1] / "installer/install.sh").read_text(encoding="utf-8")
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
install_ref=testing
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
journalctl() {{ echo 'service diagnostic'; }}
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
        self.assertIn("Counter IP: http://192.168.1.112\n", result.stdout)

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


class ServiceUnitTests(unittest.TestCase):
    def test_lgpio_works_in_a_writable_runtime_directory(self):
        source = (Path(__file__).parents[1] / "installer/install.sh").read_text(encoding="utf-8")
        unit = source[source.index("<<UNIT\n"):source.index("\nUNIT\n")].splitlines()
        # The release is read-only to the service; lgpio would fail to open the GPIO chip there.
        self.assertIn("RuntimeDirectory=counter", unit)
        self.assertIn("Environment=LG_WD=/run/counter", unit)


@unittest.skipUnless(BASH and os.name == "posix", "Release permission checks need POSIX modes")
class InstallReleasePermissionTests(unittest.TestCase):
    def test_release_directory_is_enterable_by_the_service_user(self):
        source = (Path(__file__).parents[1] / "installer/install.sh").read_text(encoding="utf-8")
        creation = source[source.index("release=$(mktemp"):source.index("step='building the Counter release'")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "releases").mkdir()
            script = f"set -euo pipefail\numask 077\ncommit={'a' * 40}\n" + creation.replace("/opt/counter", str(root)) + 'printf "%s" "$release"'
            result = subprocess.run([BASH, "-c", script], text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            # systemd's WorkingDirectory= runs as the service user, not root.
            self.assertEqual(os.stat(result.stdout).st_mode & 0o777, 0o755)


@unittest.skipUnless(BASH, "Installer launcher checks need Bash")
class InstallLauncherTests(unittest.TestCase):
    def run_launcher(self, *args, stream=False):
        source = (Path(__file__).parents[1]/'install.sh').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            installer=root/'installer'
            installer.mkdir()
            calls=root/'calls'
            quote=lambda p: shlex.quote(str(p).replace('\\','/'))
            stub="#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > "+quote(calls)+"\n"
            (installer/'install.sh').write_text(stub,encoding='utf-8',newline='\n')
            (root/'pyproject.toml').touch()
            script=root/'install.sh'
            if stream:
                model=root/'model'
                model.write_text('Raspberry Pi 5')
                source=source.replace('/proc/device-tree/model',str(model).replace('\\','/'))
                source=source.replace('/var/tmp/counter-download-XXXXXX',str(root/'download-XXXXXX').replace('\\','/'))
                setup=f"""export PATH="/usr/bin:/bin:$PATH"
git() {{
  [[ $1 == clone ]] || return 1
  target=${{!#}}
  mkdir -p "$target/installer"
  cp {quote(installer/'install.sh')} "$target/installer/install.sh"
}}
export -f git
exec bash -s -- "$@"
"""
                result=subprocess.run([BASH,'-c',setup,'counter-test','--user','pi-owner',*args],input=source,text=True,capture_output=True,timeout=20)
            else:
                script.write_text(source,encoding='utf-8',newline='\n')
                subprocess.run(['git','init','-b','testing',str(root)],check=True,capture_output=True)
                command='export PATH="/usr/bin:/bin:$PATH"; exec bash "$@"'
                result=subprocess.run([BASH,'-c',command,'counter-test',str(script).replace('\\','/'),'--user','pi-owner',*args],text=True,capture_output=True,timeout=20)
            forwarded=calls.read_text().splitlines() if calls.exists() else []
            return result,forwarded

    def test_local_testing_checkout_runs_the_pi_installer(self):
        result,args=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(args[-4:],['--branch','testing','--user','pi-owner'])

    def test_streamed_installer_downloads_requested_channel(self):
        for branch in ['main','testing']:
            with self.subTest(branch=branch):
                result,args=self.run_launcher('--branch',branch,stream=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(args[-4:],['--branch',branch,'--user','pi-owner'])
                self.assertIn('Downloading Counter / '+branch,result.stdout)

    def test_invalid_branch_cannot_start_an_installation(self):
        for branch in ['other','testing;reboot','--run']:
            result,args=self.run_launcher('--branch',branch)
            self.assertEqual(result.returncode,2)
            self.assertFalse(args)

    def test_help_does_not_install_anything(self):
        result,args=self.run_launcher('--help')
        self.assertEqual(result.returncode,0)
        self.assertIn('main|testing',result.stdout)
        self.assertFalse(args)


@unittest.skipUnless(BASH, "Update helper checks need Bash")
class UpdateHelperTests(unittest.TestCase):
    def dispatch(self,*args):
        source=(Path(__file__).parents[1]/'installer/update.sh').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'run').mkdir()
            (root/'current').mkdir()
            (root/'current'/'INSTALL_REF').write_text('testing')
            calls=root/'calls'
            quote=lambda p: shlex.quote(str(p).replace('\\','/'))
            # Simulate root-only system facilities in a disposable directory.
            source=source.replace("(( EUID == 0 )) || { echo 'The update helper must run as root.' >&2; exit 1; }",':')
            source=source.replace('/run/',str(root/'run').replace('\\','/')+'/')
            source=source.replace('/var/log/counter-update.log',str(root/'update.log').replace('\\','/'))
            source=source.replace('/opt/counter/current/INSTALL_REF',str(root/'current'/'INSTALL_REF').replace('\\','/'))
            helper=root/'helper.sh'
            helper.write_text(source,encoding='utf-8',newline='\n')
            setup=f"""export PATH="/usr/bin:/bin:$PATH"
systemctl() {{ return 1; }}
flock() {{ return 0; }}
systemd-run() {{ printf '%s\\n' "$@" > {quote(calls)}; }}
export -f systemctl flock systemd-run
exec bash "$@"
"""
            result=subprocess.run([BASH,'-c',setup,'counter-test',str(helper).replace('\\','/'),*args],text=True,capture_output=True,timeout=20)
            command=calls.read_text().splitlines() if calls.exists() else []
            return result,command

    def test_only_main_and_testing_are_dispatched(self):
        for branch in ['main','testing']:
            result,command=self.dispatch(branch)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(command[-2:],['--run',branch])
            self.assertIn('bash',command)

    def test_legacy_empty_arguments_follow_installed_channel(self):
        result,command=self.dispatch()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(command[-2:],['--run','testing'])

    def test_arbitrary_refs_and_extra_arguments_never_dispatch(self):
        for args in [('other',),('testing;reboot',),('--run',),('main','other'),('--run','other')]:
            result,command=self.dispatch(*args)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertFalse(command)

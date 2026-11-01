"""Failure-path regression tests, isolated from real networking and services."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") if os.name != "nt" else "D:/Program Files/Git/bin/bash.exe"


def posix(path):
    value = str(path).replace("\\", "/")
    if len(value) > 1 and value[1] == ":":
        value = "/" + value[0].lower() + value[2:]
    return value


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ)
        self.env.update(RM_WD_STATE_DIR=posix(self.base / "state"),
                        RM_WD_LOG=posix(self.base / "watchdog.log"),
                        TEST_CALLS=posix(self.base / "calls"), TEST_NOW="1000", TEST_HEALTH="none")
        self.stub("date", '[ "$1" = +%s ] && { echo "$TEST_NOW"; exit; }; echo test-time')
        self.stub("curl", 'case "$*" in *":$TEST_HEALTH/"*) exit 0;; *) exit 1;; esac')
        self.stub("logger", ':')
        self.stub("ubus", 'printf "%s\\n" "$*" >> "$TEST_CALLS"')

    def stub(self, name, body):
        f = self.bin / name
        f.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        f.chmod(0o755)

    def run_script(self, file):
        return subprocess.run([BASH, "-c", 'export PATH="$1:$PATH"; sh "$2"',
                               "test", posix(self.bin), posix(file)],
                              env=self.env, capture_output=True, timeout=10)

    def sample(self, seconds, health="none"):
        self.env.update(TEST_NOW=str(seconds), TEST_HEALTH=health)
        result = self.run_script(ROOT / "tunnel-watchdog.sh")
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_backup_prevents_repair_and_clears_failures(self):
        self.sample(1000)
        self.sample(1060, "18447")
        self.assertFalse((self.base / "state/tunnelwd.fails").exists())
        self.assertFalse((self.base / "calls").exists())

    def test_duplicate_calls_cooldown_and_reverse_ssh_survives(self):
        self.sample(1000)
        self.sample(1000)
        self.assertEqual((self.base / "state/tunnelwd.fails").read_text().strip(), "1")
        self.sample(1060)
        self.assertFalse((self.base / "calls").exists())
        self.sample(1120)
        first = (self.base / "calls").read_text()
        self.assertEqual(len(first.splitlines()), 5)
        self.assertNotIn('"instance":"rssh', first)
        self.sample(1180)
        self.assertEqual((self.base / "calls").read_text(), first)
        self.sample(1720)
        self.assertEqual(len((self.base / "calls").read_text().splitlines()), 10)

    def test_recovery_starts_when_discovery_all_fails(self):
        hook = self.base / "hook"
        marker = self.base / "recovery-started"
        hook.write_text('#!/bin/sh\necho started > "' + posix(marker) + '"\n', encoding="utf-8")
        hook.chmod(0o755)
        script = (ROOT / "mgmt-port-discovery.sh").read_text(encoding="utf-8")
        script = script.replace("/etc/routermgmt/tunnel-watchdog.sh", posix(hook))
        script = script.replace("/tmp/mgmt_active_port", posix(self.base / "active"))
        testfile = self.base / "discovery.sh"
        testfile.write_text(script, encoding="utf-8", newline="\n")
        result = self.run_script(testfile)
        self.assertEqual(result.returncode, 1)
        for _ in range(20):
            if marker.exists():
                break
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "all endpoints failed but recovery was skipped")


if __name__ == "__main__":
    unittest.main()

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "urls.py"
sys.path.insert(0, str(SCRIPT.parent))
import urls

FAKE_MISE = """\
#!/usr/bin/env python3
import os, pathlib, sys
platform = sys.argv[sys.argv.index("--platform") + 1]
log = os.environ.get("FAKE_MISE_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(f"{k}={v}" for k, v in sorted(os.environ.items()) if k.startswith(("MISE_", "XDG_"))) + "\\n")
if platform == "windows-arm64":
    sys.exit("mise ERROR HTTP status client error (404 Not Found) for url (https://h/x-windows-arm64.zip)")
if platform == "macos-x64":
    sys.exit("mise ERROR something else broke")
url = "https://h/x-linux.tar.gz" if platform.startswith("linux") else f"https://h/x-{platform}.zip"
alias = pathlib.Path("mise.toml").read_text().splitlines()[-1].split('"')[1]
pathlib.Path("mise.lock").write_text(
    f'[[tools."{alias}"]]\\nversion = "1.0.0"\\n'
    f'[tools.\"{alias}\"."platforms.{platform}"]\\nurl = "{url}"\\n'
)
"""


class UrlsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        (self.tools / "x.toml").write_text(
            'backend = "http:x"\ntest_version = "1.0.0"\nurl = "https://h/x-{{ os() }}"\n', encoding="utf-8"
        )
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        mise = bin_dir / "mise"
        mise.write_text(FAKE_MISE, encoding="utf-8")
        mise.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def run_urls(self, *args, env=None):
        return subprocess.run(
            [sys.executable, SCRIPT, "--tools", self.tools, *args],
            check=False,
            capture_output=True,
            text=True,
            env=env or self.env,
            cwd=self.root,
        )

    def test_dedupes_and_sorts_urls_and_reports_unpublished(self):
        result = self.run_urls("x", "1.0.0", "--platforms", "windows-x64,linux-x64,linux-x64-musl,windows-arm64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "https://h/x-linux.tar.gz\nhttps://h/x-windows-x64.zip\n")
        self.assertIn("windows-arm64: not published (https://h/x-windows-arm64.zip returned 404)", result.stderr)

    def test_other_lock_failures_are_fatal(self):
        result = self.run_urls("x", "1.0.0", "--platforms", "linux-x64,macos-x64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("macos-x64: mise lock failed", result.stderr)
        self.assertIn("something else broke", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_all_unpublished_is_an_error(self):
        result = self.run_urls("x", "1.0.0", "--platforms", "windows-arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no URLs for x 1.0.0", result.stderr)

    def test_sandboxes_mise_env(self):
        log = self.root / "mise.log"
        env = {
            **self.env,
            "FAKE_MISE_LOG": str(log),
            "MISE_URL_REPLACEMENTS": '{"a":"b"}',
            "XDG_STATE_HOME": "/real/state",
        }
        result = self.run_urls("x", "1.0.0", "--platforms", "linux-x64", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        seen = log.read_text()
        self.assertNotIn("MISE_URL_REPLACEMENTS", seen)
        self.assertIn("MISE_TRUSTED_CONFIG_PATHS=", seen)
        self.assertNotIn(str(pathlib.Path.home()), seen)
        self.assertNotIn("XDG_STATE_HOME", seen)
        state = dict(kv.split("=", 1) for kv in seen.split())["MISE_STATE_DIR"]
        self.assertTrue(state.endswith("/state") and "/real/" not in state, state)

    def test_local_tool_has_nothing_to_mirror(self):
        log = self.root / "mise.log"
        (self.tools / "local").mkdir()
        (self.tools / "local" / "in.toml").write_text('backend = "http:in"\n', encoding="utf-8")
        result = self.run_urls("in", "1.0.0", env={**self.env, "FAKE_MISE_LOG": str(log)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("in is a local tool", result.stderr)
        self.assertFalse(log.exists())

    def test_local_tool_colliding_with_upstream(self):
        (self.tools / "local").mkdir()
        (self.tools / "local" / "x.toml").write_text("", encoding="utf-8")
        result = self.run_urls("x", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local/x.toml: also defined in", result.stderr)

    def test_rejects_invalid_tool_name(self):
        result = self.run_urls("local/x", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tool 'local/x' must match", result.stderr)

    def test_version_defaults_to_test_version(self):
        log = self.root / "mise.log"
        result = self.run_urls("x", "--platforms", "linux-x64", env={**self.env, "FAKE_MISE_LOG": str(log)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "https://h/x-linux.tar.gz\n")

    def test_unknown_tool(self):
        result = self.run_urls("nope", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope.toml: no such tool", result.stderr)

    def test_invalid_tool_file(self):
        (self.tools / "bad.toml").write_text('url = "u"\n', encoding="utf-8")
        result = self.run_urls("bad", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad.toml: backend: required", result.stderr)

    def test_rejects_unsafe_version(self):
        result = self.run_urls("x", '1"\n[x]')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match", result.stderr)

    def test_uses_internet_urls_from_tool_file(self):
        text, errors = urls.registry(self.tools, "x")
        self.assertEqual(errors, [])
        self.assertEqual(text, "[tool_alias]\n\"airgap:x\" = '''http:x[url='https://h/x-{{ os() }}']'''\n")

    def test_custom_prefix_from_mirror_gives_internet_urls(self):
        mirror = self.root / "mirror.toml"
        mirror.write_text(
            'prefix = "acme"\n[[rewrite]]\nfrom = "https://h/"\nto = "https://internal/"\n', encoding="utf-8"
        )
        result = self.run_urls("x", "1.0.0", "--platforms", "linux-x64", "--mirror", mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "https://h/x-linux.tar.gz\n")

    def test_default_mirror_is_read_from_cwd(self):
        (self.root / "mirror.toml").write_text('prefix = "http"\n', encoding="utf-8")
        result = self.run_urls("x", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mirror.toml: prefix: 'http' is a mise backend name", result.stderr)

    def test_default_prefix_without_mirror(self):
        text, errors = urls.registry(self.tools, "x")
        self.assertEqual(errors, [])
        self.assertTrue(text.startswith('[tool_alias]\n"airgap:x"'))
        text, errors = urls.registry(self.tools, "x", "acme")
        self.assertTrue(text.startswith('[tool_alias]\n"acme:x"'))

    def test_invalid_prefix_in_mirror(self):
        mirror = self.root / "mirror.toml"
        mirror.write_text('prefix = "http"\n', encoding="utf-8")
        result = self.run_urls("x", "1.0.0", "--mirror", mirror)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mirror.toml: prefix: 'http' is a mise backend name", result.stderr)

    def test_lock_urls_reads_every_entry(self):
        lock = {
            "tools": {
                "airgap:x": [
                    {"version": "1", "platforms.linux-x64": {"url": "a", "checksum": "c"}},
                    {"version": "1", "options": {}, "platforms.windows-x64": {"url": "b"}},
                ],
                "airgap:y": [{"platforms.linux-x64": {"url": "other"}}],
            }
        }
        self.assertEqual(urls.lock_urls(lock, "airgap", "x"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()

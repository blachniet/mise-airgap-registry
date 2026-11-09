import os, pathlib, subprocess, sys, tempfile, unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "validate.py"
sys.path.insert(0, str(SCRIPT.parent))
import validate

FAKE_MISE = """\
#!/usr/bin/env python3
import os, pathlib, sys
with open(os.environ["FAKE_MISE_LOG"], "a") as f:
    f.write(repr(sys.argv[1:]) + " " + pathlib.Path("mise.toml").read_text().splitlines()[-1] + "\\n")
if sys.argv[1] == os.environ.get("FAKE_MISE_FAIL"):
    sys.exit(3)
"""


class ValidateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.tools = self.root / "tools"
        (self.tools / "local").mkdir(parents=True)
        self.registry = self.root / "registry.toml"
        self.registry.write_text("[tool_alias]\n", encoding="utf-8")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        mise = bin_dir / "mise"
        mise.write_text(FAKE_MISE, encoding="utf-8")
        mise.chmod(0o755)
        self.log = self.root / "mise.log"
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_MISE_LOG": str(self.log)}

    def tool(self, path, body):
        (self.tools / path).write_text(body, encoding="utf-8")

    def run_validate(self, tool, *args, **env):
        return subprocess.run(
            [sys.executable, SCRIPT, "--tools", self.tools, "--registry", self.registry, *args, tool],
            capture_output=True, text=True, env={**self.env, **env}, cwd=self.root,
        )

    def calls(self):
        return self.log.read_text().splitlines()

    def test_default_command(self):
        self.tool("x.toml", 'test_version = "1.0.0"\n')
        result = self.run_validate("x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            "['install'] \"airgap:x\" = \"1.0.0\"",
            "['exec', '--', 'x', '--version'] \"airgap:x\" = \"1.0.0\"",
        ])

    def test_prefix_from_mirror(self):
        self.tool("x.toml", 'test_version = "1.0.0"\n')
        mirror = self.root / "mirror.toml"
        mirror.write_text('prefix = "acme"\n', encoding="utf-8")
        result = self.run_validate("x", "--mirror", mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], "['install'] \"acme:x\" = \"1.0.0\"")

    def test_invalid_prefix_in_mirror(self):
        self.tool("x.toml", 'test_version = "1.0.0"\n')
        mirror = self.root / "mirror.toml"
        mirror.write_text('prefix = 1\n', encoding="utf-8")
        result = self.run_validate("x", "--mirror", mirror)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mirror.toml: prefix: must be a string", result.stderr)
        self.assertFalse(self.log.exists())

    def test_custom_command_and_local_tool(self):
        self.tool("local/y.toml", 'test_version = "2"\ntest_command = "why version --client"\n')
        result = self.run_validate("y")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("['exec', '--', 'why', 'version', '--client']", self.calls()[1])

    def test_install_failure_stops(self):
        self.tool("x.toml", 'test_version = "1"\n')
        result = self.run_validate("x", FAKE_MISE_FAIL="install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed: mise install", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_command_failure(self):
        self.tool("x.toml", 'test_version = "1"\n')
        result = self.run_validate("x", FAKE_MISE_FAIL="exec")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed: mise exec -- x --version", result.stderr)

    def test_skips_unsupported_host(self):
        self.tool("x.toml", f'test_version = "1"\nunsupported_platforms = ["{validate.host_platform()}"]\n')
        result = self.run_validate("x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"x is unsupported on {validate.host_platform()}; skipping", result.stderr)
        self.assertFalse(self.log.exists())

    def test_runs_when_mirror_supplies_unsupported_host(self):
        host = validate.host_platform()
        self.tool("x.toml", f'test_version = "1"\nunsupported_platforms = ["{host}"]\n')
        mirror = self.root / "mirror.toml"
        mirror.write_text(f'[tools.x.platforms.{host}]\nurl = "https://internal/x"\n', encoding="utf-8")
        result = self.run_validate("x", "--mirror", mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)

    def test_runs_when_another_platform_is_unsupported(self):
        other = "windows-arm64" if validate.host_platform() != "windows-arm64" else "linux-x64"
        self.tool("x.toml", f'test_version = "1"\nunsupported_platforms = ["{other}"]\n')
        result = self.run_validate("x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)

    def test_host_platform_names(self):
        self.assertIn(validate.host_platform(), validate.build.PLATFORMS)

    def test_unknown_tool(self):
        result = self.run_validate("nope")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope.toml: no such tool", result.stderr)

    def test_invalid_tool_name(self):
        result = self.run_validate("../x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match", result.stderr)


if __name__ == "__main__":
    unittest.main()

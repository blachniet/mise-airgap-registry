import pathlib, subprocess, sys, tempfile, unittest, zipfile

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "package.py"


class PackageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.registry = self.root / "registry.toml"
        self.registry.write_text("[tool_alias]\n", encoding="utf-8")

    def run_package(self, tag, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, "--registry", self.registry, "--out", self.root / "out", *args, tag],
            capture_output=True, text=True,
        )

    def test_zip_holds_only_mise_toml(self):
        result = self.run_package("v1.2.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / "out" / "registry-v1.2.3.zip") as z:
            self.assertEqual(z.namelist(), ["mise.toml"])
            self.assertEqual(z.read("mise.toml"), b"[tool_alias]\n")

    def test_reproducible(self):
        self.run_package("v1")
        first = (self.root / "out" / "registry-v1.zip").read_bytes()
        self.run_package("v1")
        self.assertEqual((self.root / "out" / "registry-v1.zip").read_bytes(), first)

    def test_rejects_bad_tag(self):
        for tag in ("../x", "a/b", ".hidden"):
            result = self.run_package(tag)
            self.assertNotEqual(result.returncode, 0, tag)
            self.assertIn("must match", result.stderr)
        self.assertFalse((self.root / "out").exists())

    def test_missing_registry(self):
        self.registry.unlink()
        result = self.run_package("v1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such file", result.stderr)


if __name__ == "__main__":
    unittest.main()

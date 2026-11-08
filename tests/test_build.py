import importlib.util, os, pathlib, subprocess, sys, tempfile, textwrap, tomllib, unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "build.py"
spec = importlib.util.spec_from_file_location("build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

HEAD = 'backend = "http:x"\ntest_version = "1.0.0"\n'


class BuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()

    def write(self, name, body):
        (self.tools / name).write_text(textwrap.dedent(body), encoding="utf-8")

    def alias(self, body):
        self.write("x.toml", HEAD + textwrap.dedent(body))
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        header, table, line = text.splitlines()
        return line

    def options(self, body):
        prefix, rest = self.alias(body).split("[", 1)
        self.assertEqual(prefix, "\"airgap:x\" = '''http:x")
        self.assertTrue(rest.endswith("]'''"))
        return rest[: -len("]'''")]

    def errors(self, files):
        for name, body in files.items():
            self.write(name, body)
        text, errors = build.build(self.tools)
        self.assertIsNone(text)
        return errors

    def error(self, body, name="x.toml"):
        errors = self.errors({name: body})
        self.assertEqual(len(errors), 1, errors)
        return errors[0]


class OutputTest(BuildTest):
    def test_full_file(self):
        self.write("b.toml", HEAD + 'url = "https://b"\n')
        self.write("a.toml", HEAD + 'url = "https://a"\n')
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        self.assertEqual(
            text,
            "# @generated from tools/*.toml - do not edit\n"
            "[tool_alias]\n"
            "\"airgap:a\" = '''http:x[url='https://a']'''\n"
            "\"airgap:b\" = '''http:x[url='https://b']'''\n",
        )

    def test_test_version_is_stripped(self):
        self.assertEqual(self.options('url = "u"'), "url='u'")

    def test_flattens_nested_tables(self):
        body = """
            url = "u"
            [platforms.windows-x64]
            url = "w"
            [platforms.linux-x64]
            bin_path = "bin"
        """
        self.assertEqual(
            self.options(body),
            "url='u',platforms.windows-x64.url='w',platforms.linux-x64.bin_path='bin'",
        )

    def test_quotes_non_bare_keys(self):
        self.assertEqual(self.options('rename_exe."age*" = "a"'), "rename_exe.'age*'='a'")

    def test_single_quotes_by_default(self):
        self.assertEqual(self.options('url = "a\\"b"'), "url='a\"b'")

    def test_double_quotes_when_value_has_single_quote(self):
        self.assertEqual(self.options("url = \"os(macos='darwin')\""), "url=\"os(macos='darwin')\"")

    def test_both_quote_types(self):
        self.assertEqual(self.options("url = \"a'b\\\"c\""), "url=\"a'b\\\"c\"")

    def test_regex_backslashes_pass_through_literal(self):
        self.assertEqual(self.options(r"version_regex = 'v(\d+)'"), r"version_regex='v(\d+)'")

    def test_regex_backslashes_escaped_in_double_quotes(self):
        self.assertEqual(self.options(r'''version_regex = "v'(\\d+)"'''), r'''version_regex="v'(\\d+)"''')

    def test_bools_and_ints(self):
        self.assertEqual(
            self.options("strip_components = 1\nprerelease = true\nshared_extraction = false"),
            "strip_components='1',prerelease='true',shared_extraction='false'",
        )

    def test_arrays(self):
        self.assertEqual(
            self.options('headers_forward.X-Test = ["a.example", "*.b.example"]'),
            "headers_forward.X-Test=['a.example','*.b.example']",
        )

    def test_mixed_scalar_array(self):
        self.assertEqual(self.options("k = [1, true, \"a'b\"]"), "k=['1','true',\"a'b\"]")


class ValidationTest(BuildTest):
    def test_missing_backend(self):
        self.assertEqual(self.error('test_version = "1"\nurl = "u"'), f"{self.tools}/x.toml: backend: required")

    def test_non_string_backend(self):
        self.assertIn("backend: must be a non-empty string", self.error('backend = 1\ntest_version = "1"\nurl = "u"'))

    def test_non_http_backend(self):
        for backend in ("github:a/b", "http:", "http:Age", "http:x[url='u']", "age"):
            with self.subTest(backend=backend):
                self.assertIn("backend:", self.error(f'backend = "{backend}"\ntest_version = "1"\nurl = "u"'))

    def test_missing_test_version(self):
        self.assertIn("test_version: required", self.error('backend = "http:x"\nurl = "u"'))

    def test_empty_test_version(self):
        for v in ('""', '" "', "1", "[]"):
            with self.subTest(v=v):
                self.assertIn("test_version: must be a non-empty string", self.error(f'backend = "http:x"\ntest_version = {v}\nurl = "u"'))

    def test_invalid_file_stem(self):
        for name in ("Age.toml", "-age.toml", "a.b.toml", "a b.toml", "_a.toml"):
            with self.subTest(name=name):
                errors = self.errors({name: HEAD + 'url = "u"'})
                (self.tools / name).unlink()
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("file name must match", errors[0])

    def test_unsupported_types(self):
        cases = {
            "d = 1979-05-27T07:32:00Z": "datetime",
            "d = 1979-05-27": "date",
            "d = 07:32:00": "time",
            "d = 1.5": "float",
        }
        for body, kind in cases.items():
            with self.subTest(body=body):
                self.assertTrue(self.error(HEAD + body).endswith(f"x.toml: d: unsupported value type {kind}"))

    def test_nested_arrays(self):
        for body in ("a = [[1]]", "a = [{ b = 1 }]", "[[a]]\nb = 1"):
            with self.subTest(body=body):
                self.assertTrue(self.error(HEAD + body).endswith("a: arrays may only contain strings, integers, and booleans"))

    def test_empty_array(self):
        self.assertTrue(self.error(HEAD + "a = []").endswith("a: empty array"))

    def test_empty_table(self):
        self.assertTrue(self.error(HEAD + 'url = "u"\n[platforms.linux-x64]').endswith("platforms.linux-x64: empty table"))

    def test_no_options(self):
        self.assertTrue(self.error(HEAD).endswith("no backend options"))

    def test_triple_quote(self):
        self.assertIn("url: ''' cannot appear", self.error(HEAD + "url = \"a'''b\""))

    def test_control_characters(self):
        self.assertIn("url: control characters", self.error(HEAD + 'url = "a\\nb"'))

    def test_bad_key(self):
        self.assertIn("control characters", self.error(HEAD + '"a\\tb" = "u"'))

    def test_invalid_toml(self):
        self.assertIn("x.toml: ", self.error("backend = "))

    def test_collects_errors_across_files(self):
        errors = self.errors({
            "a.toml": 'test_version = "1"\nd = 1.5',
            "b.toml": HEAD + "e = []",
            "c.toml": HEAD + 'url = "u"',
        })
        self.assertEqual(errors, [
            f"{self.tools}/a.toml: backend: required",
            f"{self.tools}/a.toml: d: unsupported value type float",
            f"{self.tools}/b.toml: e: empty array",
        ])

    def test_empty_tools_dir(self):
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [f"{self.tools}: no *.toml files"])

    def test_ignores_toml_directories(self):
        (self.tools / "d.toml").mkdir()
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [f"{self.tools}: no *.toml files"])
        self.write("x.toml", HEAD + 'url = "u"')
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        self.assertNotIn("airgap:d", text)


HOSTILE = r"""
backend = "http:x"
test_version = "1.0.0"
backslash = 'a\b'
sq_backslash = "a'\\b"
dq = 'a"b'
both = "a'b\"c\\d"
two_sq = "a''"
punct = "],=[{}#@"
empty = ""
unicode = "é✓ 日本"
int = 1
yes = true
no = false
list = ["a'b", 'a"b', "]", "", "a,b", 1, true]
"it's" = "k1"
"a.b" = "k2"
"" = "k3"
"ké" = "k4"
'say "hi"' = "k5"

[platforms.windows-x64]
url = "w"

[platforms."odd key".nested]
"x'y" = ["z", "a''"]
"""


def stringify(v):
    if isinstance(v, dict):
        return {k: stringify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [stringify(x) for x in v]
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


class RoundTripTest(BuildTest):
    def test_hostile_fixture(self):
        self.write("x.toml", HOSTILE)
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        backend = tomllib.loads(text)["tool_alias"]["airgap:x"]
        self.assertTrue(backend.startswith("http:x[") and backend.endswith("]"), backend)
        opts = tomllib.loads(f"t = {{{backend[len('http:x['):-1]}}}")["t"]
        expected = tomllib.loads(HOSTILE)
        del expected["backend"], expected["test_version"]
        self.assertEqual(opts, stringify(expected))


class CliTest(BuildTest):
    def run_build(self, out, env=None):
        return subprocess.run(
            [sys.executable, SCRIPT, self.tools, out], capture_output=True, text=True, env=env
        )

    def test_writes_utf8_under_non_utf8_locale(self):
        env = {**os.environ, "PYTHONUTF8": "0", "LC_ALL": "en_US.ISO8859-1"}
        env.pop("PYTHONIOENCODING", None)
        probe = subprocess.run(
            [sys.executable, "-c", "import locale; print(locale.getencoding())"],
            capture_output=True, text=True, env=env,
        )
        if "8859" not in probe.stdout:
            self.skipTest(f"ISO8859-1 locale unavailable: {probe.stdout.strip()}")
        self.write("x.toml", HEAD + 'url = "é✓"')
        out = self.root / "mise.toml"
        result = self.run_build(out, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("url='é✓'", out.read_bytes().decode("utf-8"))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_creates_output_parent(self):
        self.write("x.toml", HEAD + 'url = "u"')
        out = self.root / "dist" / "nested" / "mise.toml"
        result = self.run_build(out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\"airgap:x\" = '''http:x[url='u']'''", out.read_text(encoding="utf-8"))

    def test_errors_exit_nonzero_without_writing(self):
        self.write("x.toml", 'url = "u"')
        out = self.root / "mise.toml"
        result = self.run_build(out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x.toml: backend: required", result.stderr)
        self.assertIn("x.toml: test_version: required", result.stderr)
        self.assertFalse(out.exists())

    def test_usage(self):
        result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()

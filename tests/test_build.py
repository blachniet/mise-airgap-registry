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

    def test_test_command_is_stripped(self):
        self.assertEqual(self.options('test_command = "x version"\nurl = "u"'), "url='u'")

    def test_test_command_must_be_a_string(self):
        self.assertIn("test_command: must be a non-empty string", self.error(HEAD + 'test_command = 1\nurl = "u"'))

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


class UnsupportedTest(BuildTest):
    def opts(self, body):
        backend = tomllib.loads(f"a = {self.alias(body).split(' = ', 1)[1]}")["a"]
        return tomllib.loads(f"t = {{{backend[len('http:x['):-1]}}}")["t"]

    def test_copies_url_to_supported_platforms(self):
        opts = self.opts("""
            url = "u"
            strip_components = 1
            unsupported_platforms = ["macos-x64"]
            [platforms.windows-x64]
            url = "w"
            bin = "x.exe"
        """)
        self.assertNotIn("url", opts)
        self.assertNotIn("unsupported_platforms", opts)
        self.assertEqual(opts["strip_components"], "1")
        self.assertEqual(
            {p: o.get("url") for p, o in opts["platforms"].items()},
            {"linux-x64": "u", "linux-arm64": "u", "macos-arm64": "u", "windows-x64": "w", "windows-arm64": "u"},
        )
        self.assertEqual(opts["platforms"]["windows-x64"]["bin"], "x.exe")

    def test_keeps_platform_options_without_url(self):
        opts = self.opts('url = "u"\nunsupported_platforms = ["windows-arm64"]\n[platforms.windows-x64]\nbin = "x.exe"\n')
        self.assertEqual(opts["platforms"]["windows-x64"], {"bin": "x.exe", "url": "u"})

    def test_invalid_values(self):
        cases = {
            'unsupported_platforms = "macos-x64"': "unsupported_platforms: must be a non-empty array of strings",
            "unsupported_platforms = []": "unsupported_platforms: must be a non-empty array of strings",
            "unsupported_platforms = [1]": "unsupported_platforms: must be a non-empty array of strings",
            'unsupported_platforms = ["macos-x64-musl"]': "unsupported_platforms: unknown platform 'macos-x64-musl'",
            'unsupported_platforms = ["macos-x64", "macos-x64"]': "unsupported_platforms: duplicate platforms",
            "unsupported_platforms = [" + ",".join(f'"{p}"' for p in build.PLATFORMS) + "]": "unsupported_platforms: every platform is listed",
        }
        for body, message in cases.items():
            with self.subTest(body=body):
                self.assertIn(message, self.error(HEAD + 'url = "u"\n' + body))

    def test_requires_top_level_url(self):
        body = HEAD + 'unsupported_platforms = ["macos-x64"]\n[platforms.linux-x64]\nurl = "l"\n'
        self.assertIn("unsupported_platforms: requires a top-level url", self.error(body))

    def test_rejects_override_for_unsupported_platform(self):
        body = HEAD + 'url = "u"\nunsupported_platforms = ["macos-x64"]\n[platforms.macos-x64]\nurl = "m"\n'
        self.assertIn("platforms.macos-x64: listed in unsupported_platforms", self.error(body))

    def test_rejects_platform_aliases_and_non_tables(self):
        cases = {
            '[platforms.darwin-x64]\nurl = "m"': "platforms.darwin-x64: with unsupported_platforms, use one of",
            'platforms.linux-x64 = "s"': "platforms.linux-x64: must be a table",
            'platforms = "s"': "platforms: must be a table",
            '[platform.macos-x64]\nurl = "m"': "platform: with unsupported_platforms, use [platforms.<platform>] tables",
            'platform_macos_x64_url = "m"': "platform_macos_x64_url: with unsupported_platforms, use",
            'platforms_macos_x64_url = "m"': "platforms_macos_x64_url: with unsupported_platforms, use",
        }
        for body, message in cases.items():
            with self.subTest(body=body):
                self.assertIn(message, self.error(HEAD + 'url = "u"\nunsupported_platforms = ["macos-x64"]\n' + body))

    def test_musl_override_is_kept(self):
        opts = self.opts('url = "u"\nunsupported_platforms = ["macos-x64"]\n[platforms.linux-x64-musl]\nbin = "m"\n')
        self.assertEqual(opts["platforms"]["linux-x64-musl"], {"bin": "m"})
        self.assertEqual(opts["platforms"]["linux-x64"], {"url": "u"})


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


class MirrorTest(BuildTest):
    def mirror(self, body):
        f = self.root / "mirror.toml"
        f.write_text(textwrap.dedent(body), encoding="utf-8")
        return f

    def built(self, tool, mirror):
        self.write("x.toml", HEAD + textwrap.dedent(tool))
        text, errors = build.build(self.tools, self.mirror(mirror))
        self.assertEqual(errors, [])
        backend = tomllib.loads(text)["tool_alias"]["airgap:x"]
        return tomllib.loads(f"t = {{{backend[len('http:x['):-1]}}}")["t"]

    def mirror_errors(self, mirror, tool='url = "u"'):
        self.write("x.toml", HEAD + tool)
        text, errors = build.build(self.tools, self.mirror(mirror))
        self.assertIsNone(text)
        return errors

    def test_empty_mirror_changes_only_header(self):
        self.write("x.toml", HEAD + 'url = "https://a"\n')
        plain, _ = build.build(self.tools)
        mirrored, errors = build.build(self.tools, self.mirror("# nothing\n"))
        self.assertEqual(errors, [])
        self.assertEqual(mirrored.splitlines()[1:], plain.splitlines()[1:])
        self.assertEqual(mirrored.splitlines()[0], "# @generated from tools/*.toml and mirror.toml - do not edit")

    def test_plain_rewrite_applies_to_all_url_keys(self):
        opts = self.built(
            """\
            url = "https://github.com/o/r/a"
            version_list_url = "https://github.com/o/r/tags"
            bin = "https://github.com/not-a-url-key"
            [platforms.windows-x64]
            url = "https://github.com/o/r/w"
            """,
            """\
            [[rewrite]]
            from = "https://github.com/"
            to = "https://m.example/gh/"
            """,
        )
        self.assertEqual(opts["url"], "https://m.example/gh/o/r/a")
        self.assertEqual(opts["version_list_url"], "https://m.example/gh/o/r/tags")
        self.assertEqual(opts["bin"], "https://github.com/not-a-url-key")
        self.assertEqual(opts["platforms"]["windows-x64"]["url"], "https://m.example/gh/o/r/w")

    def test_rewrite_matches_prefix_only(self):
        opts = self.built(
            """\
            url = "https://h.example/p?mirror=https://h.example/p"
            version_list_url = "http://other/https://h.example/"
            """,
            """\
            [[rewrite]]
            from = "https://h.example/"
            to = "https://m.example/"
            """,
        )
        self.assertEqual(opts["url"], "https://m.example/p?mirror=https://h.example/p")
        self.assertEqual(opts["version_list_url"], "http://other/https://h.example/")

    def test_first_changing_rule_wins(self):
        opts = self.built(
            'url = "https://a.example/x"\n',
            """\
            [[rewrite]]
            from = "https://nomatch/"
            to = "https://never/"
            [[rewrite]]
            from = "https://a.example/"
            to = "https://b.example/"
            [[rewrite]]
            from = "https://b.example/"
            to = "https://c.example/"
            """,
        )
        self.assertEqual(opts["url"], "https://b.example/x")

    def test_tool_override_merges_after_rewrite(self):
        opts = self.built(
            """\
            url = "https://github.com/o/r/a"
            strip_components = 1
            [platforms.windows-x64]
            url = "https://github.com/o/r/w"
            """,
            """\
            [[rewrite]]
            from = "https://github.com/"
            to = "https://m.example/"
            [tools.x]
            url = "https://github.com/kept/as/written"
            [tools.x.platforms.linux-x64]
            url = "https://m.example/l"
            """,
        )
        self.assertEqual(opts["url"], "https://github.com/kept/as/written")
        self.assertEqual(opts["strip_components"], "1")
        self.assertEqual(opts["platforms"]["windows-x64"]["url"], "https://m.example/o/r/w")
        self.assertEqual(opts["platforms"]["linux-x64"]["url"], "https://m.example/l")

    def test_unsupported_expands_after_rewrite_and_override(self):
        opts = self.built(
            """\
            url = "https://github.com/o/r/a"
            unsupported_platforms = ["macos-x64"]
            """,
            """\
            [[rewrite]]
            from = "https://github.com/"
            to = "https://m.example/"
            [tools.x.platforms.linux-x64]
            url = "https://internal/l"
            """,
        )
        self.assertNotIn("url", opts)
        self.assertNotIn("macos-x64", opts["platforms"])
        self.assertEqual(opts["platforms"]["linux-x64"]["url"], "https://internal/l")
        self.assertEqual(opts["platforms"]["macos-arm64"]["url"], "https://m.example/o/r/a")

    def test_mirror_can_supply_unsupported_platform(self):
        opts = self.built(
            'url = "https://h/a"\nunsupported_platforms = ["macos-x64"]\n',
            '[tools.x.platforms.macos-x64]\nurl = "https://internal/mac"\n',
        )
        self.assertEqual(opts["platforms"]["macos-x64"], {"url": "https://internal/mac"})
        self.assertEqual(opts["platforms"]["macos-arm64"], {"url": "https://h/a"})

    def test_mirror_platform_alias_is_attributed_to_mirror(self):
        errors = self.mirror_errors('[tools.x.platforms.darwin-arm64]\nurl = "m"\n', tool='url = "u"\nunsupported_platforms = ["macos-x64"]\n')
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("platforms.darwin-arm64: with unsupported_platforms, use one of", errors[0])
        self.assertIn("(in the mirror override)", errors[0])

    def test_mirror_flat_platform_option_is_attributed_to_mirror(self):
        errors = self.mirror_errors('[tools.x]\nplatform_macos_x64_url = "m"\n', tool='url = "u"\nunsupported_platforms = ["macos-x64"]\n')
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("platform_macos_x64_url: with unsupported_platforms, use [platforms.<platform>] tables (in the mirror override)", errors[0])

    def test_unsupported_is_reserved_in_mirror(self):
        errors = self.mirror_errors('[tools.x]\nunsupported_platforms = ["macos-x64"]\n')
        self.assertIn("mirror.toml: tools.x.unsupported_platforms: set it in tools/x.toml", errors[0])

    def test_override_values_are_attributed_to_mirror(self):
        errors = self.mirror_errors(
            "[tools.x]\nurl = \"a'''b\"\n[tools.x.platforms.linux-x64]\nurl = 1.5\n"
        )
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("mirror.toml: tools.x.url: ''' cannot appear", errors[0])
        self.assertIn("mirror.toml: tools.x.platforms.linux-x64.url: unsupported value type float", errors[1])

    def test_rewrite_target_is_validated(self):
        errors = self.mirror_errors("[[rewrite]]\nfrom = 'u'\nto = \"a'''b\"\n")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("mirror.toml: rewrite[0]: ''' cannot appear", errors[0])

    def test_mirror_and_tool_errors_are_collected_together(self):
        errors = self.mirror_errors("nope = 1\n", tool="")
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("mirror.toml: nope: unknown key", errors[0])
        self.assertIn("x.toml: no backend options", errors[1])

    def test_mirror_errors(self):
        cases = {
            "nope = 1\n": "mirror.toml: nope: unknown key",
            "rewrite = 1\n": "rewrite: must be an array of tables",
            "[[rewrite]]\nfrom = 'a'\n": "rewrite[0]: must have exactly the keys from and to",
            "[[rewrite]]\nfrom = ''\nto = 'b'\n": "rewrite[0]: from must be a non-empty string",
            "tools = 1\n": "tools: must be a table",
            "[tools]\nx = 1\n": "tools.x: must be a table",
            "[tools.x]\nbackend = 'http:y'\n": "tools.x.backend: set it in tools/x.toml",
            "[tools.x]\ntest_version = '2'\n": "tools.x.test_version: set it in tools/x.toml",
            "[tools.x]\ntest_command = 'x'\n": "tools.x.test_command: set it in tools/x.toml",
            "[tools.y]\nurl = 'u'\n": "tools.y: no tools/y.toml",
            "x = [\n": "mirror.toml: ",
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                errors = self.mirror_errors(body)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(expected, errors[0])

    def test_collects_mirror_errors(self):
        errors = self.mirror_errors("nope = 1\n[[rewrite]]\nfrom = 'a'\n")
        self.assertEqual(len(errors), 2, errors)

    def test_missing_mirror_file(self):
        self.write("x.toml", HEAD + 'url = "u"')
        text, errors = build.build(self.tools, self.root / "absent.toml")
        self.assertIsNone(text)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("absent.toml", errors[0])

    def test_default_prefix_output_is_unchanged(self):
        self.write("x.toml", HEAD + 'url = "u"\n')
        plain, _ = build.build(self.tools)
        explicit, errors = build.build(self.tools, self.mirror('prefix = "airgap"\n'))
        self.assertEqual(errors, [])
        self.assertEqual(explicit.splitlines()[1:], plain.splitlines()[1:])
        self.assertIn("\"airgap:x\" = ", plain)

    def test_custom_prefix_applies_to_upstream_and_local_tools(self):
        self.write("x.toml", HEAD + 'url = "u"\n')
        (self.tools / "local").mkdir()
        self.write("local/y.toml", HEAD + 'url = "v"\n')
        text, errors = build.build(self.tools, self.mirror('prefix = "acme-2"\n'))
        self.assertEqual(errors, [])
        self.assertEqual(
            text.splitlines()[2:],
            ["\"acme-2:x\" = '''http:x[url='u']'''", "\"acme-2:y\" = '''http:x[url='v']'''"],
        )

    def test_invalid_prefixes(self):
        cases = {
            'prefix = ""': "must match",
            'prefix = "Acme"': "must match",
            'prefix = "1acme"': "must match",
            'prefix = "ac_me"': "must match",
            'prefix = "ac:me"': "must match",
            'prefix = "http"': "'http' is a mise backend name",
            'prefix = "github"': "'github' is a mise backend name",
            "prefix = 1": "prefix: must be a string",
            "prefix = ['a']": "prefix: must be a string",
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                errors = self.mirror_errors(body + "\n")
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("mirror.toml: prefix: ", errors[0])
                self.assertIn(expected, errors[0])

    def test_prefix_error_is_collected_with_others(self):
        errors = self.mirror_errors('prefix = "npm"\nnope = 1\n', tool="")
        self.assertEqual(len(errors), 3, errors)

    def test_cli_writes_nothing_on_invalid_prefix(self):
        self.write("x.toml", HEAD + 'url = "u"')
        out = self.root / "mise.toml"
        result = subprocess.run(
            [sys.executable, SCRIPT, self.tools, out, self.mirror('prefix = "aqua"\n')], capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prefix: 'aqua' is a mise backend name", result.stderr)
        self.assertFalse(out.exists())


class LocalToolsTest(BuildTest):
    def local(self, name, body):
        d = self.tools / "local"
        d.mkdir(exist_ok=True)
        (d / name).write_text(textwrap.dedent(body), encoding="utf-8")

    def mirror(self, body):
        f = self.root / "mirror.toml"
        f.write_text(textwrap.dedent(body), encoding="utf-8")
        return f

    def test_local_tools_are_merged_in_name_order(self):
        self.write("b.toml", HEAD + 'url = "https://b"\n')
        self.write("b-x.toml", HEAD + 'url = "https://bx"\n')
        self.local("a.toml", HEAD + 'url = "https://internal/a"\n')
        self.local("c.toml", HEAD + 'url = "https://internal/c"\n')
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        self.assertEqual(
            text,
            "# @generated from tools/*.toml, tools/local/*.toml - do not edit\n"
            "[tool_alias]\n"
            "\"airgap:a\" = '''http:x[url='https://internal/a']'''\n"
            "\"airgap:b-x\" = '''http:x[url='https://bx']'''\n"
            "\"airgap:b\" = '''http:x[url='https://b']'''\n"
            "\"airgap:c\" = '''http:x[url='https://internal/c']'''\n",
        )

    def test_no_local_dir_keeps_header(self):
        self.write("a.toml", HEAD + 'url = "u"\n')
        (self.tools / "local").mkdir()
        (self.tools / "local" / "README.md").write_text("x", encoding="utf-8")
        text, _ = build.build(self.tools)
        self.assertEqual(text.splitlines()[0], "# @generated from tools/*.toml - do not edit")

    def test_only_local_tools(self):
        self.local("a.toml", HEAD + 'url = "u"\n')
        text, errors = build.build(self.tools)
        self.assertEqual(errors, [])
        self.assertIn("airgap:a", text)

    def test_mirror_rewrites_skip_local_tools(self):
        self.write("up.toml", HEAD + 'url = "https://github.com/up"\n')
        self.local("in.toml", HEAD + 'url = "https://github.com/in"\n')
        text, errors = build.build(self.tools, self.mirror(
            '[[rewrite]]\nfrom = "https://github.com/"\nto = "https://m/"\n'
        ))
        self.assertEqual(errors, [])
        aliases = tomllib.loads(text)["tool_alias"]
        self.assertEqual(aliases["airgap:up"], "http:x[url='https://m/up']")
        self.assertEqual(aliases["airgap:in"], "http:x[url='https://github.com/in']")
        self.assertIn("tools/*.toml, tools/local/*.toml and mirror.toml", text.splitlines()[0])

    def test_collision_with_upstream(self):
        self.write("a.toml", HEAD + 'url = "u"\n')
        self.local("a.toml", HEAD + 'url = "v"\n')
        text, errors = build.build(self.tools)
        self.assertIsNone(text)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("local/a.toml: also defined in", errors[0])

    def test_mirror_override_of_local_tool(self):
        self.write("a.toml", HEAD + 'url = "u"\n')
        self.local("b.toml", HEAD + 'url = "v"\n')
        text, errors = build.build(self.tools, self.mirror('[tools.b]\nurl = "w"\n'))
        self.assertIsNone(text)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("tools.b: local tool; edit tools/local/b.toml instead", errors[0])

    def test_local_tools_are_validated(self):
        self.write("a.toml", HEAD + 'url = "u"\n')
        self.local("Bad.toml", 'url = 1.5\n')
        text, errors = build.build(self.tools)
        self.assertIsNone(text)
        joined = "\n".join(errors)
        for expected in ("file name must match", "backend: required", "test_version: required", "unsupported value type float"):
            self.assertIn(expected, joined)
        self.assertTrue(all("local/Bad.toml" in e for e in errors), errors)


class CliTest(BuildTest):
    def run_build(self, out, env=None, *extra):
        return subprocess.run(
            [sys.executable, SCRIPT, self.tools, out, *extra], capture_output=True, text=True, env=env
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

    def test_mirror_argument(self):
        self.write("x.toml", HEAD + 'url = "https://a/x"')
        mirror = self.root / "mirror.toml"
        mirror.write_text('[[rewrite]]\nfrom = "https://a/"\nto = "https://b/"\n', encoding="utf-8")
        out = self.root / "mise.toml"
        result = self.run_build(out, None, mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("url='https://b/x'", out.read_text(encoding="utf-8"))

    def test_usage(self):
        result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()

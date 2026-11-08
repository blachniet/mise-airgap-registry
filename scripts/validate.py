"""Install one tool's test_version in a sandbox and run its test_command."""

import argparse
import pathlib
import shlex
import subprocess
import sys
import tomllib

import build
import sandbox


def spec_file(tools, name):
    for f in (tools / f"{name}.toml", tools / build.LOCAL / f"{name}.toml"):
        if f.is_file():
            return f
    sys.exit(f"{tools / f'{name}.toml'}: no such tool")


def run(cmd, proj, env):
    print(f"$ {shlex.join(cmd)}", flush=True)
    if subprocess.run(cmd, cwd=proj, env=env, check=False).returncode:
        sys.exit(f"failed: {shlex.join(cmd)}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="name of a tools/<tool>.toml or tools/local/<tool>.toml file")
    parser.add_argument("--tools", type=pathlib.Path, default=pathlib.Path("tools"))
    parser.add_argument("--registry", type=pathlib.Path, default=pathlib.Path("dist/mise.toml"))
    build.mirror_arg(parser)
    args = parser.parse_args(argv[1:])
    if not build.ALIAS_NAME.fullmatch(args.tool):
        sys.exit(f"tool {args.tool!r} must match {build.ALIAS_NAME.pattern}")
    prefix, errors = build.read_prefix(args.mirror)
    if errors:
        sys.exit("\n".join(errors))
    spec = tomllib.loads(spec_file(args.tools, args.tool).read_text(encoding="utf-8"))
    command = shlex.split(spec.get("test_command", f"{args.tool} --version"))
    registry = args.registry.read_text(encoding="utf-8")
    with sandbox.project(registry, {f"{prefix}:{args.tool}": spec["test_version"]}) as (proj, env):
        run(["mise", "install"], proj, env)
        run(["mise", "exec", "--", *command], proj, env)


if __name__ == "__main__":
    main(sys.argv)

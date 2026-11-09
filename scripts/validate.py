"""Install one tool's test_version in a sandbox and run its test_command."""
import argparse, pathlib, platform, shlex, subprocess, sys, tomllib

import build, sandbox


def spec_file(tools, name):
    for f in (tools / f"{name}.toml", tools / build.LOCAL / f"{name}.toml"):
        if f.is_file():
            return f
    sys.exit(f"{tools / f'{name}.toml'}: no such tool")


def host_platform():
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system(), platform.system())
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def run(cmd, proj, env):
    print(f"$ {shlex.join(cmd)}", flush=True)
    if subprocess.run(cmd, cwd=proj, env=env).returncode:
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
    (_, overrides, prefix), errors = build.load_mirror(args.mirror) if args.mirror else (((), {}, build.DEFAULT_PREFIX), [])
    if errors:
        sys.exit("\n".join(f"{args.mirror}: {e}" for e in errors))
    spec = tomllib.loads(spec_file(args.tools, args.tool).read_text(encoding="utf-8"))
    host = host_platform()
    supplied = overrides.get(args.tool, {}).get("platforms", {})
    if host in spec.get("unsupported_platforms", []) and host not in supplied:
        print(f"{args.tool} is unsupported on {host}; skipping", file=sys.stderr)
        return
    command = shlex.split(spec.get("test_command", f"{args.tool} --version"))
    registry = args.registry.read_text(encoding="utf-8")
    with sandbox.project(registry, {f"{prefix}:{args.tool}": spec["test_version"]}) as (proj, env):
        run(["mise", "install"], proj, env)
        run(["mise", "exec", "--", *command], proj, env)


if __name__ == "__main__":
    main(sys.argv)

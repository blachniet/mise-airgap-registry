"""Print the internet URLs to mirror for one tools/<name>.toml at a version."""
import argparse, concurrent.futures, pathlib, re, subprocess, sys, tomllib

import build, sandbox

PLATFORMS = (
    "linux-x64", "linux-arm64", "linux-x64-musl", "linux-arm64-musl",
    "macos-x64", "macos-arm64", "windows-x64", "windows-arm64",
)
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
NOT_FOUND = re.compile(r"404 Not Found\) for url \((\S+?)\)")


def registry(tools, name, prefix=build.DEFAULT_PREFIX):
    f, local = tools / f"{name}.toml", tools / build.LOCAL / f"{name}.toml"
    if local.is_file():
        if f.is_file():
            return None, [f"{local}: also defined in {f}"]
        return "", []
    if not f.is_file():
        return None, [f"{f}: no such tool"]
    line, errors = build.alias(f, prefix=prefix)
    if errors:
        return None, [f"{f}: {e}" for e in errors]
    return f"[tool_alias]\n{line}\n", []


def lock_urls(lock, prefix, name):
    return [
        v["url"]
        for entry in lock.get("tools", {}).get(f"{prefix}:{name}", [])
        for k, v in entry.items()
        if k.startswith("platforms.") and isinstance(v, dict) and "url" in v
    ]


# A single 404 fails a multi-platform lock, and --dry-run reports no URLs, so each platform gets a real lock.
def lock(text, prefix, name, version, platform):
    with sandbox.project(text, {f"{prefix}:{name}": version}) as (proj, env):
        result = subprocess.run(
            ["mise", "lock", "--platform", platform], cwd=proj, env=env, capture_output=True, text=True
        )
        if result.returncode:
            missing = NOT_FOUND.search(result.stderr)
            if missing:
                return platform, [], f"not published ({missing[1]} returned 404)"
            raise RuntimeError(f"{platform}: mise lock failed:\n{result.stderr.strip()}")
        urls = lock_urls(tomllib.loads((proj / "mise.lock").read_text(encoding="utf-8")), prefix, name)
        if not urls:
            return platform, [], "not supported (no URL for this platform)"
        return platform, urls, None


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="name of a tools/<tool>.toml file")
    parser.add_argument("version", nargs="?", help="defaults to the tool's test_version")
    parser.add_argument("--tools", type=pathlib.Path, default=pathlib.Path("tools"))
    build.mirror_arg(parser)
    parser.add_argument("--platforms", default=",".join(PLATFORMS), help="comma-separated")
    args = parser.parse_args(argv[1:])
    if not build.ALIAS_NAME.fullmatch(args.tool):
        sys.exit(f"tool {args.tool!r} must match {build.ALIAS_NAME.pattern}")
    prefix, errors = build.read_prefix(args.mirror)
    if errors:
        sys.exit("\n".join(errors))
    text, errors = registry(args.tools, args.tool, prefix)
    if errors:
        sys.exit("\n".join(errors))
    if not text:
        print(f"{args.tool} is a local tool ({args.tools / build.LOCAL}); nothing to mirror", file=sys.stderr)
        return
    if args.version is None:
        args.version = tomllib.loads((args.tools / f"{args.tool}.toml").read_text(encoding="utf-8"))["test_version"]
    if not VERSION.fullmatch(args.version):
        sys.exit(f"version {args.version!r} must match {VERSION.pattern}")
    platforms = [p for p in args.platforms.split(",") if p]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        jobs = [pool.submit(lock, text, prefix, args.tool, args.version, p) for p in platforms]
        try:
            results = sorted(j.result() for j in jobs)
        except RuntimeError as e:
            sys.exit(str(e))
    urls = sorted({u for _, us, _ in results for u in us})
    for platform, _, skipped in results:
        if skipped:
            print(f"{platform}: {skipped}", file=sys.stderr)
    if not urls:
        sys.exit(f"no URLs for {args.tool} {args.version} on any platform")
    print("\n".join(urls))


if __name__ == "__main__":
    main(sys.argv)

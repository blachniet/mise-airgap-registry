"""Print the internet URLs to mirror for one tools/<name>.toml at a version."""
import argparse, concurrent.futures, os, pathlib, re, subprocess, sys, tempfile, tomllib

import build

PLATFORMS = (
    "linux-x64", "linux-arm64", "linux-x64-musl", "linux-arm64-musl",
    "macos-x64", "macos-arm64", "windows-x64", "windows-arm64",
)
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
NOT_FOUND = re.compile(r"404 Not Found\) for url \((\S+?)\)")


def registry(tools, name):
    f = tools / f"{name}.toml"
    if not f.is_file():
        return None, [f"{f}: no such tool"]
    line, errors = build.alias(f)
    if errors:
        return None, [f"{f}: {e}" for e in errors]
    return f"[tool_alias]\n{line}\n", []


def lock_urls(lock, name):
    return [
        v["url"]
        for entry in lock.get("tools", {}).get(f"airgap:{name}", [])
        for k, v in entry.items()
        if k.startswith("platforms.") and isinstance(v, dict) and "url" in v
    ]


def sandbox_env(root, proj):
    # Inherited MISE_* (task context, url_replacements, global settings) would leak into the lock.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MISE_")}
    env.update(
        HOME=str(root / "home"),
        MISE_DATA_DIR=str(root / "data"),
        MISE_CACHE_DIR=str(root / "cache"),
        MISE_CONFIG_DIR=str(root / "config"),
        MISE_TRUSTED_CONFIG_PATHS=str(proj),
    )
    return env


def lock(text, name, version, platform):
    with tempfile.TemporaryDirectory() as tmp:
        # macOS temp dirs sit behind a /var -> /private/var symlink, which breaks the trust match.
        root = pathlib.Path(tmp).resolve()
        proj = root / "proj"
        proj.mkdir()
        (proj / "registry.toml").write_text(text, encoding="utf-8")
        (proj / "mise.toml").write_text(
            f'include = ["registry.toml"]\n[tools]\n"airgap:{name}" = "{version}"\n', encoding="utf-8"
        )
        (proj / "mise.lock").touch()
        result = subprocess.run(
            ["mise", "lock", "--platform", platform],
            cwd=proj, env=sandbox_env(root, proj), capture_output=True, text=True,
        )
        if result.returncode:
            missing = NOT_FOUND.search(result.stderr)
            if missing:
                return platform, [], f"not published ({missing[1]} returned 404)"
            raise RuntimeError(f"{platform}: mise lock failed:\n{result.stderr.strip()}")
        urls = lock_urls(tomllib.loads((proj / "mise.lock").read_text(encoding="utf-8")), name)
        return platform, urls, None


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="name of a tools/<tool>.toml file")
    parser.add_argument("version")
    parser.add_argument("--tools", type=pathlib.Path, default=pathlib.Path("tools"))
    parser.add_argument("--platforms", default=",".join(PLATFORMS), help="comma-separated")
    args = parser.parse_args(argv[1:])
    if not VERSION.fullmatch(args.version):
        sys.exit(f"version {args.version!r} must match {VERSION.pattern}")
    text, errors = registry(args.tools, args.tool)
    if errors:
        sys.exit("\n".join(errors))
    platforms = [p for p in args.platforms.split(",") if p]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        jobs = [pool.submit(lock, text, args.tool, args.version, p) for p in platforms]
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

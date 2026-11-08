---
name: add-tool
description: Add a new tools/<name>.toml entry to this airgap registry. Discovers the tool's release source, asset naming and archive layout, writes http backend options, verifies install and lock in a sandbox, and lists the URLs to mirror.
---

# Add a tool to the airgap registry

Each `tools/<name>.toml` becomes alias `airgap:<name>` in `dist/mise.toml` (committed, built by `mise run build`). `tools/*.toml` always hold the internet URLs; forks map them to internal locations in `mirror.toml`, never in `tools/`. See `tools/*.toml` for working examples of every pattern below.

## 1. Discover

1. Find release sources. Check GitHub (`gh release view --repo <owner>/<repo> --json tagName,assets --jq '.tagName, (.assets[].name)'`) and whether the vendor has its own download site. **If both exist, ask the user which to use.** Non-GitHub hosts: remind the user to confirm the airgap proxy mirrors that host.
   - HashiCorp: `curl -sf 'https://api.releases.hashicorp.com/v1/releases/<product>?limit=20'` (quote it; `?` globs in zsh). Skip `+ent*` and prerelease versions; recent pages can be all `+ent`.
   - Astral (uv): `https://releases.astral.sh/github/<repo>/releases/download/<ver>/...` (no owner segment; `astral-sh/uv` 404s).
2. Pick `test_version`: latest stable release.
3. Note the tag shape: `v1.2.3`, bare `1.2.3` (uv, HashiCorp), or `<name>-1.2.3` (jq).
4. Map each target to an asset: linux/macos/windows × x64/arm64, plus gnu/musl if Linux assets are split. Note which targets don't exist (e.g. packer/vault have no windows-arm64).
5. If the release offers several formats (raw binary, .tar.gz, .zip), **ask the user which to use.**
6. Download one archive per distinct format into a scratchpad dir and list it (`tar tzvf`, `unzip -l`): top-level dir? binary name? executable bit? Windows zips often differ from the tarballs (uv: tarball has `uv-<target>/`, zip is flat).

## 2. Write `tools/<name>.toml`

```toml
backend = "http:<name>"
test_version = "<ver>"
url = "https://.../{{version}}/<name>_{{ os(macos='darwin') }}_{{ arch(x64='amd64') }}.tar.gz"

[platforms.windows-x64]
url = "https://.../<name>_windows_amd64.zip"
```

- File stem must match `[a-z0-9][a-z0-9_-]*`. `backend` must be `http:<name>`. `test_version` is required and is not emitted.
- One templated `url`, plus `[platforms.<os>-<arch>]` overrides only for exceptions. Any option can be overridden per platform.
- Tera: `os()` → linux/macos/windows, `arch()` → x64/arm64, both take rename kwargs. Use single quotes inside `{{ }}` (the TOML string is double-quoted).
- libc split: prefer gnu, with musl only on musl hosts: `{{ os(linux='unknown-linux-' ~ libc(), macos='apple-darwin') }}`. Don't hard-code musl. `mise lock` treats `linux-x64` and `linux-x64-musl` as separate targets.
- Archive with one top-level dir: `strip_components = 1`; override to `0` per platform if that platform's archive is flat.
- Archive whose binary has a platform-suffixed name or no exec bit (terragrunt): `rename_exe = "<name>"`.
- Raw binary: `bin = "<name>"`; Windows overrides need `bin = "<name>.exe"`.
- Format (`.zip`/`.tar.gz`) is auto-detected; no `format` needed.
- Missing platform: omit it; don't add an override.
- build.py doesn't check option names. mise rejects unknown ones at install: "option `x` is not used by the http backend".

## 3. Verify

```sh
uv run scripts/build.py tools dist/mise.toml mirror.toml   # or: mise run build
S=<scratchpad>/<name>sb; rm -rf $S; mkdir -p $S/proj; cp dist/mise.toml $S/proj/registry.toml; cd $S/proj
export HOME=$S/home MISE_DATA_DIR=$S/data MISE_CACHE_DIR=$S/cache MISE_CONFIG_DIR=$S/cfg MISE_TRUSTED_CONFIG_PATHS=$S/proj
printf 'include = ["registry.toml"]\n[tools]\n"airgap:<name>" = "<ver>"\n' > mise.toml; touch mise.lock
mise install && ls -l $S/data/installs/airgap-<name>/<ver>/ && mise exec -- <name> --version
mise lock --platform linux-x64,linux-arm64,linux-x64-musl,linux-arm64-musl,macos-arm64,macos-x64,windows-x64,windows-arm64
grep -o 'https://[^"]*' mise.lock | grep -v mise.jdx.dev | sort -u
```

- Always use the isolated sandbox. Write `[tools]` by hand; **never `mise use 'airgap:x@v'`**. In mise 2026.11.x it fails with "Http backend requires 'url' option", and for a tool already in `[tools]` it inlines the registry's options into the project file.
- Pass all 8 platforms to `mise lock`; the default skips windows-arm64. Drop platforms the tool doesn't publish.
- A lock can list the tool more than once (`[[tools."airgap:x"]]`) when options differ per platform. That's expected.
- A 404 during lock names the bad URL; fix the template or override.
- Large downloads (vault) can exceed the default 2-minute Bash timeout; raise it.
- Some tools phone home on `--version` (sops prints "(latest)"). Note that for the user.
- Then in the repo: `mise run build` and `mise run test`. If `dist/mise.toml` breaks, every mise command fails; recover with `uv run scripts/build.py tools dist/mise.toml mirror.toml` or `git checkout dist/`.

## 4. Finish

- `git add tools/<name>.toml dist/mise.toml`. Don't commit.
- Report: the entry, the test version, platforms that aren't published, the gotchas you hit, and the de-duplicated list of URLs to mirror from the lock step.

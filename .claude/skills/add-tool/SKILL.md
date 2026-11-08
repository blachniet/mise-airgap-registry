---
name: add-tool
description: Add a new tools/<name>.toml entry to this airgap registry. Discovers the tool's release source, asset naming and archive layout, writes http backend options, verifies install and lock in a sandbox, and lists the URLs to mirror.
---

# Add a tool to the airgap registry

Each `tools/<name>.toml` becomes alias `airgap:<name>` in `dist/mise.toml` (committed, built by `mise run build`). `tools/*.toml` always hold the internet URLs; forks map them to internal locations in `mirror.toml`, never in `tools/`. A fork's internal-only tool (not on the internet) goes in `tools/local/<name>.toml` with its internal URL; upstream never adds files there, and `mirror.toml` doesn't apply to it. Skip the internet discovery steps for those. See `tools/*.toml` for working examples of every pattern below.

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
- `test_command` (optional, not emitted) defaults to `<name> --version`. Set it when the binary or flag differs, e.g. `"tofu --version"`, `"kubectl version --client"`.
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
mise run build
mise run validate <name>   # sandboxed install of test_version, then test_command
mise run urls <name>       # every platform's URL; 404s reported as not published
mise run test
```

- Both tasks run in an isolated sandbox. **Never `mise use 'airgap:x@v'`**: in mise 2026.11.x it fails with "Http backend requires 'url' option", and for a tool already in `[tools]` it inlines the registry's options into the project file.
- CI runs `validate` on Linux, macOS and Windows, so check the Windows overrides carefully even though you can't run them locally.
- `mise run urls` reports each 404 platform as not published. If the tool does publish that platform, fix the template or override.
- Large downloads (vault) can exceed the default 2-minute Bash timeout; raise it.
- Some tools phone home on `--version` (sops prints "(latest)"). Note that for the user.
- If `dist/mise.toml` breaks, every mise command fails; recover with `uv run scripts/build.py tools dist/mise.toml mirror.toml` or `git checkout dist/`.

## 4. Finish

- `git add tools/<name>.toml dist/mise.toml`. Don't commit.
- Report: the entry, the test version, platforms that aren't published, the gotchas you hit, and the URLs to mirror from `mise run urls <name> <ver>` (internet URLs; it ignores `mirror.toml`).

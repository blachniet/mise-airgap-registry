---
name: add-tool
description: Add a new tools/<name>.toml entry to this airgap registry. Discovers the tool's release source, asset naming and archive layout, writes http backend options, verifies install and lock in a sandbox, and lists the URLs to mirror.
---

# Add a tool to the airgap registry

Each `tools/<name>.toml` becomes alias `airgap:<name>` in `dist/mise.toml` (`airgap:` stands for `prefix` in `mirror.toml`). `tools/*.toml` hold internet URLs only. A fork's internal-only tool goes in `tools/local/<name>.toml` with its internal URL; skip discovery for those. See `tools/*.toml` for working examples of every pattern below.

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

- `test_command` (not emitted) defaults to `<name> --version`; set it when that doesn't work, e.g. `"kubectl version --client"`.
- One templated `url`, plus `[platforms.<os>-<arch>]` overrides only for exceptions.
- Tera: `os()` → linux/macos/windows, `arch()` → x64/arm64, both take rename kwargs. Use single quotes inside `{{ }}` (the TOML string is double-quoted).
- libc split: `{{ os(linux='unknown-linux-' ~ libc(), macos='apple-darwin') }}`. Don't hard-code musl; `mise lock` treats `linux-x64` and `linux-x64-musl` as separate targets.
- Archive with one top-level dir: `strip_components = 1`; override to `0` per platform if that platform's archive is flat.
- Archive whose binary has a platform-suffixed name or no exec bit (terragrunt): `rename_exe = "<name>"`.
- Raw binary: `bin = "<name>"`; Windows overrides need `bin = "<name>.exe"`.
- Missing platform: `unsupported_platforms = ["macos-x64"]` (not emitted; names as in `[platforms.*]`). Without it the 404 breaks `mise install`/`mise lock` on every host, since they lock all common platforms. Installing on the listed platform itself still fails; mention that to the user.

## 3. Verify

```sh
mise run build
mise run validate <name>   # sandboxed install of test_version, then test_command
mise run urls <name>       # every platform's URL
mise run test
```

- Both tasks run in a sandbox. **Never `mise use 'airgap:x@v'`** (see AGENTS.md).
- CI runs `validate` on Linux, macOS and Windows, so check the Windows overrides carefully even though you can't run them locally.
- A 404 on any platform (in `validate` or `urls`) means a wrong template or override, or a platform the tool doesn't publish: add that to `unsupported_platforms`.
- Large downloads (vault) can exceed the default 2-minute Bash timeout; raise it.
- Some tools phone home on `--version` (sops prints "(latest)"). Note that for the user.

## 4. Finish

- `git add tools/<name>.toml dist/mise.toml`. Don't commit.
- Report: the entry, the test version, platforms that aren't published, the gotchas you hit, and the URLs to mirror from `mise run urls <name> <ver>` (internet URLs; it ignores `mirror.toml`).

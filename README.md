# Mise Air Gap Registry

Use [mise](https://mise.jdx.dev) inside an air gap the way you would on the internet: one `include` line, short tool names, and lockfiles that work, with no GitHub API and no hand-copied download config. Built entirely on mise's own [`http` backend](https://mise.jdx.dev/dev-tools/backends/http.html) and [tool aliases](https://mise.jdx.dev/dev-tools/aliases.html).

- **Using the registry?** Start with [Using the registry](#using-the-registry).
- **Running the mirror for an air-gapped network?** See [Maintaining a fork](#maintaining-a-fork).

## Why

mise's `aqua:` and `github:` backends need `api.github.com`, which doesn't exist inside an air gap. The usual workaround is hand-written `http:` config, copy-pasted into every project and slowly drifting apart.

This registry replaces all of that with one line:

- **Install by name**: `"airgap:terraform" = "1.17.1"`, with no per-project download config.
- **Lockfiles that work offline**: `mise lock` records URLs and checksums, with no GitHub API calls.
- **Fails loudly**: if the registry isn't loaded, mise errors out instead of quietly trying the internet.

For fork maintainers:

- **Know exactly what to mirror**: `mise run urls <tool> <version>` lists every artifact across platforms.
- **Host artifacts anywhere**: rewrite URLs to match your mirror's layout without touching the tool definitions.
- **Add internal tools**: tools that exist only inside your air gap.
- **Add tools in minutes**: the bundled Claude Code skill writes and tests new entries.

## Using the registry

### Getting started

[Include](https://mise.jdx.dev/configuration.html#include) your registry in your project's `mise.toml`, then add tools with the `airgap:` prefix (a fork may use a different prefix; ask your registry's maintainers). Use the git URL of the registry your organization provides (this repository, or a fork of it), and pin a tag or commit so the registry only changes when you choose:

```toml
include = ["git::<REGISTRY_GIT_URL>//dist/mise.toml?ref=<TAG>"]

[tools]
"airgap:terraform" = "1.17.1"
"airgap:jq" = "1.8.2"
```

For example, the upstream registry is `git::https://github.com/blachniet/mise-airgap-registry.git//dist/mise.toml?ref=main`.

```sh
mise install
mise lock   # optional: record URLs and checksums in mise.lock
```

See [`tools/`](tools/) for the available tools.

> [!IMPORTANT]
> Add tools by editing `[tools]` directly. `mise use 'airgap:<tool>@<version>'` currently fails, or copies the tool's settings into your `mise.toml`.

### Troubleshooting

- **`airgap is not a valid plugin name`** (or your fork's prefix): mise didn't load the registry. Check the `include` line in your `mise.toml`.
- **`a config include must be a git:: URL`**: replace `<REGISTRY_GIT_URL>` and `<TAG>` in the `include` line.
- **A tool or version won't install**: the artifact may not be in your mirror yet. Ask your registry's maintainers.

## Maintaining a fork

Fork this repository when your air gap needs its own configuration: artifacts at different URLs in your mirror, or internal tools. Keep pulling from upstream to get new tools. Your changes live in `mirror.toml` and `tools/local/`, which upstream leaves alone, so merges stay clean. You'll need to rebuild `dist/mise.toml` after each merge.

Run the examples below from a clone of your fork.

### Choose the alias prefix

Set `prefix` in [`mirror.toml`](mirror.toml) to name your registry's aliases (`acme:terraform`) instead of `airgap:terraform`, then rebuild. It must match `[a-z][a-z0-9-]*` and must not be a mise backend name such as `http` or `github`. Use a unique org-style name, not a mise tool or plugin name such as `gh` or `uv`.

```toml
# mirror.toml
prefix = "acme"
```

Choose it once, when you create the fork. Changing it later breaks consumers' `[tools]` entries, lockfiles and install directories. This repository's own `mise.toml` uses `airgap:` tool names, so edit them too after changing the prefix.

### Find artifacts that need to be mirrored

```sh
mise run urls terraform 1.17.1
```

This prints every internet URL for that version across Linux (glibc and musl), macOS and Windows. Platforms the tool doesn't publish are noted on stderr.

### Rewrite URLs

If your mirror serves artifacts somewhere other than their internet URLs, set rewrite rules in [`mirror.toml`](mirror.toml) and rebuild. `tools/*.toml` keep the internet URLs, so `mise run urls` still lists what to mirror.

```toml
# mirror.toml
[[rewrite]]
from = "https://github.com/"
to = "https://registry.example.com/github.com/"

[[rewrite]]
from = "https://releases.hashicorp.com/"
to = "https://registry.example.com/hashicorp/"

# Override any option for a single tool
[tools.sops]
url = "https://registry.example.com/sops/v{{version}}/sops.{{ os(macos='darwin') }}.{{ arch(x64='amd64') }}"
```

```sh
mise run build
```

Each rule replaces a URL prefix. Rules apply in order to every URL option (`url`, `platforms.*.url`, `*_url`), and the first rule that matches wins. See the comments in `mirror.toml` for details.

### Add internal tools

Tools that exist only inside your air gap go in [`tools/local/`](tools/local/), using the same format as `tools/*.toml` and pointing at their URL in your mirror:

```toml
# tools/local/acme-cli.toml
backend = "http:acme-cli"
test_version = "2.3.0"
url = "https://registry.example.com/acme/acme-cli/{{version}}/acme-cli-{{ os() }}-{{ arch() }}.tar.gz"
```

```sh
mise run build
```

Registry users install them like any other tool: `"airgap:acme-cli" = "2.3.0"` (with your prefix). Names must not clash with `tools/*.toml`, and `mirror.toml` doesn't apply to them.

### Add a new tool

Tools that are available on the internet go in `tools/`. Consider contributing them upstream so every fork gets them.

With [Claude Code](https://claude.com/claude-code), run the bundled skill. It works out where the tool is downloaded from and what its release files look like, writes the entry, and tests it:

```
/add-tool helm
```

Or add `tools/<name>.toml` by hand. Options are the [`http` backend options](https://mise.jdx.dev/dev-tools/backends/http.html), with a per-platform override for anything that differs:

```toml
# tools/age.toml
backend = "http:age"
test_version = "1.2.1"   # version used for testing; not passed to mise
url = "https://github.com/FiloSottile/age/releases/download/v{{version}}/age-v{{version}}-{{ os(macos='darwin') }}-{{ arch(x64='amd64') }}.tar.gz"
strip_components = 1

[platforms.windows-x64]
url = "https://github.com/FiloSottile/age/releases/download/v{{version}}/age-v{{version}}-windows-amd64.zip"
```

If the tool doesn't publish a platform, list it in `unsupported_platforms` (any of `linux-x64`, `linux-arm64`, `macos-x64`, `macos-arm64`, `windows-x64`, `windows-arm64`). Otherwise, in any project with a `mise.lock`, `mise install` and `mise lock` fail on every machine with a 404, because they lock the common platforms, not just the current one. With it, `mise lock` skips that platform; on that platform itself `mise install` fails with "No URL for platform", so don't pin the tool in projects used there. `unsupported_platforms` needs a top-level `url`. Platform overrides must use the names above (or `linux-x64-musl`, `linux-arm64-musl`), and an unsupported platform can't have one in `tools/` (a fork with its own build can add it in `mirror.toml`). For example:

```toml
# tools/hk.toml has no Intel macOS build
url = "https://github.com/jdx/hk/releases/download/v{{version}}/hk-{{ arch(x64='x86_64', arm64='aarch64') }}-{{ os(linux='unknown-linux-' ~ libc(), macos='apple-darwin') }}.tar.gz"
unsupported_platforms = ["macos-x64"]
```

### Build and test

After any change, rebuild the registry and commit `dist/mise.toml` along with your changes:

```sh
mise run build
mise run test
```

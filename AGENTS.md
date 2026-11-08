# AGENTS.md

- Never hand-edit `dist/mise.toml`; edit `tools/`, `tools/local/` or `mirror.toml`, then run `mise run build`.
- A broken `dist/mise.toml` breaks every mise command here, including `mise run build` (it's included, and uv comes from `airgap:uv`; a fork with another `prefix` in `mirror.toml` must rename the `airgap:` tools in `mise.toml`); recover with `git checkout dist/` or `python3 scripts/build.py tools dist/mise.toml mirror.toml`.
- Keep internet URLs in `tools/*.toml`; internal URLs go in `mirror.toml`, internal-only tools in `tools/local/` (see README "Maintaining a fork").
- The alias prefix is `prefix` in `mirror.toml` (default `airgap`), read by `scripts/build.py`; `airgap:` below stands for it. Never hard-code it in scripts.
- Never run `mise use 'airgap:<tool>@<ver>'`; mise 2026.11.x fails or inlines the alias options. Edit `[tools]` by hand.
- Test mise behavior only in a sandbox with temp `HOME`, `MISE_{DATA,CACHE,CONFIG,STATE}_DIR` and `MISE_TRUSTED_CONFIG_PATHS` (see `scripts/sandbox.py`).
- Push the OCI artifact from inside `dist/` as `mise.toml:application/toml`: mise reads `mise.toml` from the unpacked artifact, so the layer must be titled `mise.toml`.

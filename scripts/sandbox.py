"""Throwaway mise projects that can't read or write the caller's mise state."""

import contextlib
import os
import pathlib
import tempfile


def env(root, proj):
    # Inherited MISE_* (task context, url_replacements, global settings) would leak into the sandbox,
    # and XDG_* would point mise's state at the caller's real directories.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MISE_", "XDG_"))}
    env.update(
        HOME=str(root / "home"),
        MISE_DATA_DIR=str(root / "data"),
        MISE_CACHE_DIR=str(root / "cache"),
        MISE_CONFIG_DIR=str(root / "config"),
        MISE_STATE_DIR=str(root / "state"),
        MISE_TRUSTED_CONFIG_PATHS=str(proj),
    )
    return env


@contextlib.contextmanager
def project(registry, tools):
    """Yield (proj, env) for a project that includes `registry` text and pins `tools` ({alias: version})."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        # macOS temp dirs sit behind a /var -> /private/var symlink, which breaks the trust match.
        root = pathlib.Path(tmp).resolve()
        proj = root / "proj"
        proj.mkdir()
        (proj / "registry.toml").write_text(registry, encoding="utf-8")
        pins = "".join(f'"{alias}" = "{version}"\n' for alias, version in tools.items())
        (proj / "mise.toml").write_text(f'include = ["registry.toml"]\n[tools]\n{pins}', encoding="utf-8")
        yield proj, env(root, proj)

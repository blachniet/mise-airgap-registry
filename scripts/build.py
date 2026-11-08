"""Flatten tools/*.toml into a mise [tool_alias] registry file."""
import pathlib, re, sys, tomllib

PREFIX = "airgap"
HEADER = "# @generated from {} - do not edit"
ALIAS_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
BACKEND = re.compile(rf"http:{ALIAS_NAME.pattern}")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
REGEX_PREFIX = "regex:"
RESERVED = ("backend", "test_version")
LOCAL = "local"


class SpecError(Exception):
    pass


def flatten(d, path=()):
    for k, v in d.items():
        if isinstance(v, dict) and v:
            yield from flatten(v, path + (k,))
        else:
            yield path + (k,), v


def quote(s):
    if CONTROL_CHARS.search(s):
        raise SpecError("control characters are not supported")
    if "'''" in s:
        raise SpecError("''' cannot appear inside the TOML literal string")
    if "'" not in s:
        return f"'{s}'"
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def scalar(v):
    if isinstance(v, bool):
        return quote(str(v).lower())
    if isinstance(v, (int, str)):
        return quote(str(v))
    raise SpecError(f"unsupported value type {type(v).__name__}")


def value(v):
    if isinstance(v, dict):
        raise SpecError("empty table")
    if not isinstance(v, list):
        return scalar(v)
    if not v:
        raise SpecError("empty array")
    if any(isinstance(x, (list, dict)) for x in v):
        raise SpecError("arrays may only contain strings, integers, and booleans")
    return "[" + ",".join(scalar(x) for x in v) + "]"


def key(path):
    return ".".join(k if BARE_KEY.fullmatch(k) else quote(k) for k in path)


def pop_string(spec, name, errors):
    v = spec.pop(name, None)
    if v is None:
        errors.append(f"{name}: required")
    elif not isinstance(v, str) or not v.strip():
        errors.append(f"{name}: must be a non-empty string")
    else:
        return v


def is_url_key(k):
    return k == "url" or k.endswith("_url")


def rewrite_url(url, rules):
    for apply in rules:
        new = apply(url)
        if new != url:
            return new
    return url


def rewrite_urls(spec, rules):
    for k, v in spec.items():
        if isinstance(v, dict):
            rewrite_urls(v, rules)
        elif isinstance(v, str) and is_url_key(k):
            spec[k] = rewrite_url(v, rules)


def merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v


def rule(r):
    if not isinstance(r, dict) or set(r) != {"from", "to"}:
        raise SpecError("must have exactly the keys from and to")
    src, to = r["from"], r["to"]
    if not (isinstance(src, str) and src) or not isinstance(to, str):
        raise SpecError("from must be a non-empty string and to a string")
    quote(to)
    if not src.startswith(REGEX_PREFIX):
        return lambda url: url.replace(src, to)
    try:
        pattern = re.compile(src[len(REGEX_PREFIX):])
    except re.error as e:
        raise SpecError(f"invalid regex: {e}")
    # A function replacement keeps `to` literal: no backreferences or escapes.
    return lambda url: pattern.sub(lambda m: to, url, count=1)


def override_errors(o):
    for path, v in flatten(o):
        try:
            key(path), value(v)
        except SpecError as e:
            yield f"{'.'.join(path)}: {e}"


def load_mirror(f):
    try:
        mirror = tomllib.loads(f.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return ([], {}), [str(e)]
    errors = [f"{k}: unknown key" for k in mirror if k not in ("rewrite", "tools")]
    rewrites = mirror.get("rewrite", [])
    if not isinstance(rewrites, list):
        errors.append("rewrite: must be an array of tables")
        rewrites = []
    rules = []
    for i, r in enumerate(rewrites):
        try:
            rules.append(rule(r))
        except SpecError as e:
            errors.append(f"rewrite[{i}]: {e}")
    tables = mirror.get("tools", {})
    if not isinstance(tables, dict):
        errors.append("tools: must be a table")
        tables = {}
    overrides = {}
    for name, o in tables.items():
        if not isinstance(o, dict):
            errors.append(f"tools.{name}: must be a table")
            continue
        errs = [f"{k}: set it in tools/{name}.toml" for k in RESERVED if k in o]
        errs += override_errors(o)
        errors += [f"tools.{name}.{e}" for e in errs]
        if not errs:
            overrides[name] = o
    return (rules, overrides), errors


def alias(f, rules=(), override=None):
    errors = []
    if not ALIAS_NAME.fullmatch(f.stem):
        errors.append(f"file name must match {ALIAS_NAME.pattern}.toml")
    try:
        spec = tomllib.loads(f.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return None, errors + [str(e)]
    backend = pop_string(spec, "backend", errors)
    if backend and not BACKEND.fullmatch(backend):
        errors.append(f"backend: {backend!r} must match {BACKEND.pattern}")
    pop_string(spec, "test_version", errors)
    rewrite_urls(spec, rules)
    merge(spec, override or {})
    if not spec:
        errors.append("no backend options")
    opts = []
    for path, v in flatten(spec):
        try:
            opts.append(f"{key(path)}={value(v)}")
        except SpecError as e:
            errors.append(f"{'.'.join(path)}: {e}")
    if errors:
        return None, errors
    return f'"{PREFIX}:{f.stem}" = \'\'\'{backend}[{",".join(opts)}]\'\'\'', []


def toml_files(d):
    return sorted(f for f in d.glob("*.toml") if f.is_file())


def override_target_errors(overrides, upstream, local, src):
    for n in overrides:
        if n in local:
            yield f"tools.{n}: local tool; edit {src.name}/{LOCAL}/{n}.toml instead"
        elif n not in upstream:
            yield f"tools.{n}: no {src.name}/{n}.toml"


def build(src, mirror=None):
    upstream, local = toml_files(src), toml_files(src / LOCAL)
    if not upstream + local:
        return None, [f"{src}: no *.toml files"]
    upstream_stems = {f.stem for f in upstream}
    errors = [f"{f}: also defined in {src / f.name}" for f in local if f.stem in upstream_stems]
    rules, overrides = (), {}
    sources = f"{src.name}/*.toml"
    if local:
        sources += f", {src.name}/{LOCAL}/*.toml"
    if mirror:
        (rules, overrides), errs = load_mirror(mirror)
        errs += override_target_errors(overrides, upstream_stems, {f.stem for f in local}, src)
        errors += [f"{mirror}: {e}" for e in errs]
        sources += f" and {mirror.name}"
    aliases = []
    for f in upstream:
        line, errs = alias(f, rules, overrides.get(f.stem))
        aliases.append((f.name, line))
        errors += [f"{f}: {e}" for e in errs]
    # Local tools already point at internal URLs, so mirror rewrites never apply.
    for f in local:
        line, errs = alias(f)
        aliases.append((f.name, line))
        errors += [f"{f}: {e}" for e in errs]
    if errors:
        return None, errors
    lines = [HEADER.format(sources), "[tool_alias]"] + [line for _, line in sorted(aliases)]
    return "\n".join(lines) + "\n", []


def main(argv):
    if len(argv) not in (3, 4):
        sys.exit(f"usage: {argv[0]} <tools-dir> <output> [<mirror.toml>]")
    src, out = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    mirror = pathlib.Path(argv[3]) if len(argv) == 4 else None
    text, errors = build(src, mirror)
    if errors:
        sys.exit("\n".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)


if __name__ == "__main__":
    main(sys.argv)

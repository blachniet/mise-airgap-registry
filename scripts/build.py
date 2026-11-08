"""Flatten tools/*.toml into a mise [tool_alias] registry file."""
import pathlib, re, sys, tomllib

PREFIX = "airgap"
HEADER = "# @generated from tools/*.toml - do not edit"
ALIAS_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
BACKEND = re.compile(rf"http:{ALIAS_NAME.pattern}")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


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


def alias(f):
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


def build(src):
    files = sorted(f for f in src.glob("*.toml") if f.is_file())
    if not files:
        return None, [f"{src}: no *.toml files"]
    lines, errors = [HEADER, "[tool_alias]"], []
    for f in files:
        line, errs = alias(f)
        lines.append(line)
        errors += [f"{f}: {e}" for e in errs]
    if errors:
        return None, errors
    return "\n".join(lines) + "\n", []


def main(argv):
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <tools-dir> <output>")
    src, out = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    text, errors = build(src)
    if errors:
        sys.exit("\n".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)


if __name__ == "__main__":
    main(sys.argv)

"""Write registry-<tag>.zip, holding only the registry as mise.toml, for moving it into an air gap."""
import argparse, pathlib, re, sys, zipfile

TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, used in the zip name")
    parser.add_argument("--registry", type=pathlib.Path, default=pathlib.Path("dist/mise.toml"))
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("release"))
    args = parser.parse_args(argv[1:])
    if not TAG.fullmatch(args.tag):
        sys.exit(f"tag {args.tag!r} must match {TAG.pattern}")
    if not args.registry.is_file():
        sys.exit(f"{args.registry}: no such file (run mise run build)")
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"registry-{args.tag}.zip"
    # A fixed timestamp and mode keep the zip byte-identical for the same registry.
    info = zipfile.ZipInfo("mise.toml", date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(info, args.registry.read_bytes())
    print(target)


if __name__ == "__main__":
    main(sys.argv)

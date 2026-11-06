"""Entry point: python -m ossutil_tui   (log in on first start, then it remembers you)."""
from __future__ import annotations

import argparse
import sys

from .app import FaDiApp
from .context import AppContext, AppPaths
from .profile import DpapiProtector, ProfileError, ProfileStore
from .state import StateCorruptError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fadi-oss-tui", description="TUI for large uploads with ossutil 2.x")
    parser.add_argument("--app-dir", default=None, help="folder for profile, queue, logs and checkpoints (default: LocalAppData\\FaDi-OSS-TUI)")
    parser.add_argument("--exe", default="", help="path to ossutil, pre-filled on the login screen")
    parser.add_argument("--part-size-mb", type=int, default=4, help="preferred part size in MiB (default 4)")
    parser.add_argument("--ascii", action="store_true", help="ASCII borders and bars only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.part_size_mb <= 5120:
        print("error: --part-size-mb must be between 1 and 5120", file=sys.stderr)
        return 2
    try:
        protector = DpapiProtector()
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    paths = AppPaths.default(args.app_dir)
    context = AppContext(
        paths,
        ProfileStore(paths.profile_file, protector),
        part_size=args.part_size_mb * 1024 * 1024,
        unicode=not args.ascii,
        exe_hint=args.exe,
    )
    try:
        context.resume_session()
    except StateCorruptError as exc:
        print(f"error: {exc}. Fix or move the file; it was not modified.", file=sys.stderr)
        return 2
    FaDiApp(context).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
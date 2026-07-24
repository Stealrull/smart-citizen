"""Standalone reset tool for the Blueprint Log Scan watermark.

Smart Citizen's "Scan Logs for Owned Blueprints" (Blueprint Tracker tab)
remembers the newest blueprint-received event it has already imported (the
"watermark") so a re-scan only picks up genuinely new blueprints instead of
re-reading the same Game.log lines every time. There is currently no button
to clear that watermark, so a player who wants to force a full re-scan (for
example, to re-check further back through logbackups\\, or after a scanning
fix) has no way to do it themselves.

This tool clears ONLY the blueprint scan watermark for the channel(s) you
choose. It does not touch your owned-items list, user.ini overrides, or any
other Smart Citizen setting -- items already marked owned stay marked owned.
The next "Scan Logs for Owned Blueprints" click in Smart Citizen then
re-parses Game.log and logbackups\\*.log from scratch for that channel.

A permanent "Reset Scan" button is planned for a future release; this tool
is a stopgap until then.

Works with both the installed (registry) and portable versions of Smart
Citizen -- it auto-detects which one you're using.

Usage:
    python scripts/blueprint_rescan_tool/reset_blueprint_scan.py [--yes] [--channel LIVE]

Run with no arguments to be prompted before each channel with a watermark
set is reset.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo root importable when run as a plain script. Skipped when
# frozen (PyInstaller) -- __file__ then points flat into the extracted
# _MEIPASS bundle, and the "src" package is already importable directly via
# PyInstaller's own import hooks.
if not getattr(sys, "frozen", False):
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from src.utils.settings import AppSettings


def _watermark_key(channel: str) -> str:
    return f"{AppSettings.BLUEPRINT_LOG_WATERMARK}/{channel}"


def _this_tools_own_dir() -> Path:
    """Directory this tool is running from, frozen or not."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_portable_data_dirs(start_dir: Path | None = None) -> list[Path]:
    """Auto-discover portable installs' config.json with zero user input.

    Same discovery heuristics as the 2.2.0 name-collision repair tool
    (scripts/repair_tool/repair_name_collisions.py), duplicated rather than
    imported so this tool stays a single self-contained script.
    """
    exe_dir = start_dir or _this_tools_own_dir()

    search_dirs: set[Path] = {exe_dir, exe_dir.parent}
    for base in (exe_dir, exe_dir.parent):
        try:
            search_dirs.update(p for p in base.iterdir() if p.is_dir())
        except OSError:
            pass
    for name in ("Desktop", "Downloads"):
        home_dir = Path.home() / name
        if home_dir.is_dir():
            search_dirs.add(home_dir)
            try:
                search_dirs.update(p for p in home_dir.iterdir() if p.is_dir())
            except OSError:
                pass

    found: dict[Path, float] = {}
    for d in search_dirs:
        try:
            portable_exes = list(d.glob("SmartCitizen-Portable-*.exe"))
        except OSError:
            portable_exes = []
        for exe_path in portable_exes:
            config = exe_path.parent / "data" / "config.json"
            if config.exists():
                found[config] = config.stat().st_mtime

        for config in (d / "data" / "config.json", d / "config.json"):
            if config.exists():
                found[config] = config.stat().st_mtime

    return sorted(found, key=lambda c: found[c], reverse=True)


def resolve_portable_config_path(user_input: str) -> Path | None:
    """Locate a portable install's config.json from whatever the user typed."""
    raw = user_input.strip().strip('"')
    if not raw:
        return None
    p = Path(raw)

    candidates = []
    if p.is_file():
        candidates.append(p.parent / "data" / "config.json")
    candidates.append(p / "data" / "config.json")
    candidates.append(p / "config.json")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _reset_channel(channel: str, *, auto_yes: bool) -> bool:
    """Clear *channel*'s watermark if one is set. Returns True if cleared."""
    AppSettings.set_active_channel(channel)
    current = AppSettings.get_blueprint_log_watermark()
    if current is None:
        print(f"  {channel}: no scan watermark set -- nothing to reset.")
        return False

    print(f"  {channel}: watermark set to {current.isoformat()}")
    if not auto_yes:
        reply = input(
            f"  Clear it so the next scan re-reads {channel}'s logs from scratch? [y/N] "
        ).strip().lower()
        if reply not in ("y", "yes"):
            print("  Skipped.")
            return False

    AppSettings.settings().remove(_watermark_key(channel))
    AppSettings.settings().sync()
    print("  Cleared. Owned blueprints are untouched -- only the scan watermark was reset.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="Clear watermarks without prompting for confirmation.")
    parser.add_argument("--channel", help="Only reset this channel (default: check all channels).")
    parser.add_argument("--no-pause", action="store_true", help="Don't wait for Enter before exiting.")
    parser.add_argument(
        "--data-dir",
        help="Path to a PORTABLE Smart Citizen install's data folder (or its .exe/folder) -- "
             "skips the interactive prompt for scripted use.",
    )
    args = parser.parse_args()

    try:
        channels = [args.channel] if args.channel else list(AppSettings.AVAILABLE_CHANNELS)

        root = AppSettings.get_sc_install_root()
        if not root:
            config_path = None
            if args.data_dir:
                config_path = resolve_portable_config_path(args.data_dir)
            else:
                auto_found = find_portable_data_dirs()
                if auto_found:
                    config_path = auto_found[0]
                    print(f"Found a portable Smart Citizen install -- using its settings: {config_path}")
                    if len(auto_found) > 1:
                        others = ", ".join(str(c) for c in auto_found[1:])
                        print(f"(Also found: {others}. Re-run with --data-dir <path> if that's the wrong one.)")
                elif not args.yes:
                    print(
                        "Could not find Smart Citizen settings through the normal (registry) path "
                        "(this happens if you're using the PORTABLE version -- portable installs "
                        "keep their settings in their own folder, not Windows)."
                    )
                    answer = input(
                        "If you're using the portable version, paste the path to its .exe "
                        "or folder here (or press Enter to skip): "
                    ).strip()
                    config_path = resolve_portable_config_path(answer) if answer else None
                    if answer and not config_path:
                        print(f"Could not find a config.json near {answer!r} -- check the path and try again.")

            if config_path:
                from src.utils.json_settings import JsonSettings
                AppSettings._backend = JsonSettings(config_path)
                print(f"Using portable settings: {config_path}")

        print("Smart Citizen Blueprint Scan reset tool")
        print(f"Channels to check: {', '.join(channels)}\n")

        original_channel = AppSettings.get_active_channel()
        cleared = 0
        try:
            for channel in channels:
                try:
                    if _reset_channel(channel, auto_yes=args.yes):
                        cleared += 1
                except Exception as e:
                    print(f"  Error while resetting {channel}: {e}")
        finally:
            AppSettings.set_active_channel(original_channel)

        print(f"\nDone. {cleared} channel(s) reset.")
        if cleared:
            print('Open Smart Citizen\'s Blueprint Tracker tab and click "Scan Logs for Owned Blueprints" again.')
    finally:
        if not args.no_pause:
            input("\nPress Enter to exit...")

    return 0


if __name__ == "__main__":
    sys.exit(main())

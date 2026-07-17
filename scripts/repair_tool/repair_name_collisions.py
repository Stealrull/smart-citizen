"""Standalone repair tool for the 2.2.0-and-earlier name-collision bug (#255/#257).

Smart Citizen versions through 2.2.0 ran ``sync_key_variants`` over the
ENTIRE loc-string table instead of just the ``item_Name*``/``item_Desc*``
keys it was actually meant for. Two unrelated CIG keys that happened to
canonicalize to the same string after stripping underscores and lowercasing
would silently overwrite each other -- most visibly ``Stanton2`` (the
Crusader planet's name key) losing its value to ``Stanton_2`` (the star,
"Stanton (Star)"), so the starmap showed the wrong label. A real base.ini
audit found ~14 more collisions of the same shape: comm-array button text,
a mission title format string, ship-interior area names, Options-menu
turret labels, two distinct thruster names, and a kiosk label.

This tool does NOT require updating Smart Citizen. It re-runs the FIXED
merge/apply pipeline (this script imports the corrected
``src.merger.ini_merger`` straight from this checkout) against your
EXISTING cached base.ini, user.ini, and settings, and rewrites your
installed global.ini with the corrected values. Any key the old bug never
touched comes out byte-identical; only the handful of collided keys
change. As a side effect it also writes the UTF-8 BOM the game's own
loc-string loader needs (#261) if your installed file is missing one.

Usage:
    python scripts/repair_tool/repair_name_collisions.py [--yes] [--channel LIVE]

Run with no arguments to be prompted before each channel is repaired.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Make the repo root importable when run as a plain script. Skipped when
# frozen (PyInstaller) -- __file__ then points flat into the extracted
# _MEIPASS bundle, and the "src" package is already importable directly
# via PyInstaller's own import hooks, no sys.path surgery needed.
if not getattr(sys, "frozen", False):
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from src.utils.settings import AppSettings
from src.parser.ini_parser import load_sources_from_settings, parse_ini_file
from src.merger.ini_merger import merge_sources_by_hierarchy, merge_ini_files
from src.utils.applied_file_validator import validate_applied_file
from src.utils.owned_items import apply_owned_to_value
from src.utils.version import get_version
from src.models.string_model import StringEntry

# Mirrors src/gui/main_window.py's _stamp_journal_entries / _stamp_frontend_version
# / their supporting regexes, deliberately duplicated (not imported) so this
# standalone tool never has to pull in PyQt6's GUI widget stack just to
# reuse two pure-dict helper functions -- keeps the packaged EXE small and
# its import graph independent of the rest of the app. Keep in sync with
# main_window.py if either ever changes (same tolerated-duplication shape as
# CATEGORY_SUBTREES <-> DATAFORGE_KEEP_SUBPATHS elsewhere in this codebase).
_JOURNAL_STAMP_RE = re.compile(r"(?:\\n)*\[Edited with Smart Citizen v[^\]]+\]\s*$")
_JOURNAL_TITLE_KEY_RE = re.compile(r"_(?:title|shorttitle|subtitle|subheading|from)(?:,P)?$", re.IGNORECASE)
_SENTINEL_MISSING = object()
_FRONTEND_VERSION_KEY = "Frontend_PU_Version"
_FRONTEND_VERSION_STAMP_RE = re.compile(
    r"\s*\|\s*(?:Localizations Enhanced (?:with|by)|Enhanced with <3 by)\s+Smart Citizen\s+v?[^\s|]+\s*$"
)


def _stamp_frontend_version(merged: dict) -> dict:
    if _FRONTEND_VERSION_KEY not in merged:
        return merged
    base = _FRONTEND_VERSION_STAMP_RE.sub("", merged[_FRONTEND_VERSION_KEY]).rstrip()
    merged[_FRONTEND_VERSION_KEY] = f"{base} | Localizations Enhanced with Smart Citizen v{get_version()}"
    return merged


def _stamp_journal_entries(merged: dict, stock: dict | None = None) -> dict:
    version = get_version()
    new_stamp = f"\\n\\n[Edited with Smart Citizen v{version}]"
    stock = stock or {}
    out: dict = {}
    for key, value in merged.items():
        if StringEntry.extract_category(key) != "Journal":
            out[key] = value
            continue
        if _JOURNAL_TITLE_KEY_RE.search(key):
            out[key] = value
            continue
        unstamped = _JOURNAL_STAMP_RE.sub("", value).rstrip()
        if stock and stock.get(key, _SENTINEL_MISSING) == unstamped:
            out[key] = unstamped
            continue
        out[key] = unstamped + new_stamp
    return out


def _build_repaired_merge(sources_dict: dict, hierarchy: list[str], user_overrides: dict) -> dict:
    """Reproduce main_window.py's apply_to_game merge steps exactly, minus the GUI bits.

    Deliberately skips main_window.py's "include discovered items" filtering
    step: that only controls which rows the in-app grid displays. A
    discovered/"New" key has no matching line in base.ini by definition, and
    merge_ini_files only ever replaces values on lines base.ini already has
    -- so whether such a key is present in the overrides dict has zero
    effect on what actually gets written to global.ini.
    """
    merged = merge_sources_by_hierarchy(sources_dict, hierarchy, user_overrides)

    owned = AppSettings.get_owned_items()
    if owned:
        for key, value in list(merged.items()):
            new_value = apply_owned_to_value(value, owned)
            if new_value != value:
                merged[key] = new_value

    stock_dict = sources_dict.get(AppSettings.SOURCE_GLOBAL, {})
    merged = _stamp_journal_entries(merged, stock_dict)
    merged = _stamp_frontend_version(merged)
    return merged


def _backup_global_ini(target_path: Path) -> Path | None:
    if not target_path.exists():
        return None
    backup_dir = AppSettings.get_backups_dir()
    backup_files = sorted(backup_dir.glob("global.ini.bak_*"), key=lambda f: f.stat().st_mtime)
    if len(backup_files) >= 5:
        backup_files[0].unlink()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"global.ini.bak_{timestamp}"
    shutil.copy2(target_path, backup_path)
    return backup_path


def _repair_channel(channel: str, *, auto_yes: bool) -> None:
    print(f"\n=== {channel} ===")
    AppSettings.set_active_channel(channel)

    base_file = AppSettings.get_base_ini_path()
    target_path = AppSettings.get_global_ini_path()

    if not base_file.exists():
        print(f"  Skipping: no cached base.ini ({base_file}) -- run Extract in Smart Citizen first.")
        return
    if not target_path.exists():
        print(f"  Skipping: no applied global.ini found ({target_path}) -- nothing to repair.")
        return

    current_applied = parse_ini_file(target_path)

    sources_dict, hierarchy, _ = load_sources_from_settings()
    user_ini_path = AppSettings.get_user_ini_path()
    user_overrides = parse_ini_file(user_ini_path, strip_values=False) if user_ini_path.exists() else {}

    merged = _build_repaired_merge(sources_dict, hierarchy, user_overrides)

    changed = {
        key: (current_applied.get(key, ""), value)
        for key, value in merged.items()
        if current_applied.get(key, "") != value
    }

    if not changed:
        print("  Nothing to repair -- your applied global.ini already matches the fixed output.")
        return

    print(f"  Found {len(changed)} key(s) that differ from the corrected merge:")
    for key, (old, new) in list(changed.items())[:10]:
        old_disp = (old[:60] + "...") if len(old) > 60 else old
        new_disp = (new[:60] + "...") if len(new) > 60 else new
        print(f"    {key}:")
        print(f"      before: {old_disp!r}")
        print(f"      after:  {new_disp!r}")
    if len(changed) > 10:
        print(f"    ... and {len(changed) - 10} more")

    if not auto_yes:
        reply = input(f"  Apply this fix to {channel}'s global.ini? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("  Skipped.")
            return

    backup_path = _backup_global_ini(target_path)
    if backup_path:
        print(f"  Backed up existing file to {backup_path}")

    merge_ini_files(str(base_file), merged, str(target_path))

    stock_keys = set(sources_dict.get(AppSettings.SOURCE_GLOBAL, {}).keys()) or None
    validation_msg = validate_applied_file(target_path, AppSettings.get_cache_dir(), stock_keys=stock_keys)
    if validation_msg:
        print(f"  Validation FAILED, rolling back: {validation_msg}")
        try:
            target_path.unlink()
        except OSError:
            pass
        if backup_path and backup_path.exists():
            shutil.copy2(backup_path, target_path)
            print("  Restored previous file from backup.")
        return

    print(f"  Repaired {len(changed)} key(s) in {target_path}")


def _detect_installed_channels(root: str) -> list[str]:
    """Channels that actually have a Data.p4k under *root*.

    Deliberately NOT AppSettings.get_available_channels(): that function
    returns every known channel name as a UI-dropdown placeholder when
    *root* is empty, so a combo box is never blank -- treating that
    placeholder list as "real, installed channels" is exactly what caused
    this tool to iterate all 5 channel names on an unconfigured machine,
    creating empty Documents\\Smart Citizen\\<channel>\\cache\\ folders for
    channels that were never actually installed anywhere.
    """
    root_path = Path(root)
    return [ch for ch in AppSettings.AVAILABLE_CHANNELS if (root_path / ch / "Data.p4k").exists()]


def resolve_portable_config_path(user_input: str) -> Path | None:
    """Locate a portable install's config.json from whatever the user typed.

    Accepts the SmartCitizen-Portable-*.exe path itself, the folder next to
    it, or the ``data`` folder directly -- users are as likely to paste any
    one of these as the others.
    """
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


def _resolve_channels(root: str, explicit_channel: str | None) -> tuple[list[str], list[str]]:
    """Return (channels_to_process, installed_channels) for *root*."""
    installed = _detect_installed_channels(root) if root else []
    channels = [explicit_channel] if explicit_channel else installed
    if explicit_channel and explicit_channel not in installed:
        print(f"Warning: no Data.p4k found for channel {explicit_channel!r} under {root} -- continuing anyway.")
    return channels, installed


def _channel_has_cached_data(channel: str) -> bool:
    """Check for a cached base.ini WITHOUT touching AppSettings.get_base_ini_path()
    / get_cache_dir() -- both mkdir the channel's cache folder as a side
    effect just from being called, which is exactly the bug this tool
    already had to fix once (creating empty cache folders for channels
    that were never really configured). Mirrors get_base_ini_path()'s own
    English-vs-other-language path shape without that side effect.
    """
    language = AppSettings.get_selected_language()
    channel_dir = AppSettings.get_user_data_dir() / channel
    if language == AppSettings.DEFAULT_LANGUAGE:
        base_ini = channel_dir / "cache" / "base.ini"
    else:
        base_ini = channel_dir / "cache" / "lang" / language / "base.ini"
    return base_ini.exists()


def _any_channel_has_cached_data(channels: list[str]) -> bool:
    return any(_channel_has_cached_data(ch) for ch in channels)


def _this_tools_own_dir() -> Path:
    """Directory this tool is running from, frozen or not."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_portable_data_dirs(start_dir: Path | None = None) -> list[Path]:
    """Auto-discover portable installs' config.json with zero user input.

    Covers the layouts a user is actually likely to have without asking
    them anything: this tool dropped right next to (or inside, or one
    level above) an already-extracted SmartCitizen-Portable-*/ folder, or
    sitting on the Desktop/Downloads alongside one. Read-only, shallow
    (one level of iterdir() per search root) -- not a filesystem crawl.

    Returns every distinct config.json found, most-recently-modified
    first (a reasonable "which one are they actually using" guess when
    more than one turns up).
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="Apply fixes without prompting for confirmation.")
    parser.add_argument("--channel", help="Only repair this channel (default: all installed channels).")
    parser.add_argument("--no-pause", action="store_true", help="Don't wait for Enter before exiting.")
    parser.add_argument(
        "--data-dir",
        help="Path to a PORTABLE Smart Citizen install's data folder (or its .exe/folder) -- "
             "skips the interactive prompt for scripted use.",
    )
    args = parser.parse_args()

    exit_code = 0
    try:
        root = AppSettings.get_sc_install_root()
        channels, installed_channels = _resolve_channels(root, args.channel) if root else ([], [])

        # Trigger the portable fallback not just when nothing was found at
        # all, but also when a real game install WAS found yet none of its
        # channels have any cached data -- that's exactly what a stale
        # user_data_dir override looks like (a separate, independent
        # setting from sc_install_root): the game is detected correctly,
        # but Smart Citizen's own working data lives somewhere else, most
        # likely a portable install with its own separate settings file.
        if not channels or not _any_channel_has_cached_data(channels):
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
                        "Could not find cached Smart Citizen data through the normal (registry) settings "
                        "(this happens if you're using the PORTABLE version of Smart Citizen -- "
                        "portable installs keep their settings in their own folder, not Windows)."
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
                root = AppSettings.get_sc_install_root()
                channels, installed_channels = _resolve_channels(root, args.channel) if root else ([], [])

        if not root:
            print(
                "Could not find your Star Citizen install path. Open Smart Citizen, "
                "confirm the install path is set on the Config tab, then run this tool again."
            )
            exit_code = 1
        else:
            print(f"Star Citizen install path: {root}")

            if not channels:
                print("No installed Star Citizen channels found under that path (no Data.p4k in LIVE/PTU/etc.).")
                exit_code = 1
            else:
                print("Smart Citizen name-collision repair tool")
                print(f"Channels to check: {', '.join(channels)}")

                original_channel = AppSettings.get_active_channel()
                try:
                    for channel in channels:
                        try:
                            _repair_channel(channel, auto_yes=args.yes)
                        except Exception as e:
                            print(f"  Error while repairing {channel}: {e}")
                finally:
                    AppSettings.set_active_channel(original_channel)

                print("\nDone.")
    finally:
        if not args.no_pause:
            input("\nPress Enter to exit...")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

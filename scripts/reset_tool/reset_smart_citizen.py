"""Smart Citizen Reset Tool.

Standalone hotfix utility for the installer (registry) build of Smart
Citizen. Removes every trace of the app's settings and user data so a
fresh install starts completely clean. The Windows uninstaller only
removes the program files, it never touches the registry key or the
Documents / AppData folders the app writes to.

Deletes, if present:
  - HKCU\\Software\\Osiris DevWorks\\Smart Citizen        (current settings)
  - HKCU\\Software\\Osiris DevWorks\\SC Localization Editor (pre-0.9.0 legacy settings)
  - The resolved user-data folder (default Documents\\Smart Citizen\\;
    honors a USER_DATA_DIR/UserDataDir registry override if one was set)
  - The legacy pre-rebrand Documents\\SC Localization Editor\\ folder,
    if a very old install never got the chance to migrate it
  - The resolved DataForge XML cache folder (default
    %LOCALAPPDATA%\\Smart Citizen\\; honors a CACHE_DIR override)
  - %TEMP%\\SmartCitizen-Update\\ (leftover auto-updater downloads)

Does NOT touch anything under the Star Citizen game install itself
(global.ini, user.cfg), those are the player's applied localization
files, not app settings, and are left alone on purpose.

Usage:
    reset_smart_citizen.exe            interactive: shows what will be
                                        deleted and asks for confirmation
    reset_smart_citizen.exe --yes      skip the confirmation prompt
    reset_smart_citizen.exe --dry-run  show what would be deleted, delete nothing
"""
import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

ORG_NAME = "Osiris DevWorks"
APP_NAME = "Smart Citizen"
LEGACY_APP_NAME = "SC Localization Editor"

USER_DATA_DIR_KEY = "user_data_dir"
USER_DATA_DIR_ALIAS = "UserDataDir"
CACHE_DIR_KEY = "cache_dir"

PROCESS_NAMES = ("SmartCitizen.exe",)


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _read_reg_value(subkey_path: str, value_name: str) -> "str | None":
    """Read a single string value from HKCU\\Software\\<subkey_path>, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey_path) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            value = str(value).strip()
            return value or None
    except OSError:
        return None


def _reg_key_exists(subkey_path: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey_path):
            return True
    except OSError:
        return False


def _delete_reg_tree(subkey_path: str) -> bool:
    """Recursively delete HKCU\\Software\\<subkey_path>. Returns True if it existed."""
    if not _reg_key_exists(subkey_path):
        return False

    def _recurse(path: str) -> None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            subkeys = []
            i = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
            for name in subkeys:
                _recurse(f"{path}\\{name}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)

    _recurse(subkey_path)
    return True


def _resolve_docs_base() -> Path:
    """Resolve the real Documents root (honors OneDrive redirection)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            return Path(winreg.QueryValueEx(key, "Personal")[0])
    except OSError:
        return Path.home() / "Documents"


def resolve_user_data_dir() -> Path:
    """Mirrors AppSettings.get_user_data_dir()'s resolution order (registry mode)."""
    app_key = f"Software\\{ORG_NAME}\\{APP_NAME}"
    override = _read_reg_value(app_key, USER_DATA_DIR_KEY) or _read_reg_value(
        app_key, USER_DATA_DIR_ALIAS
    )
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return _resolve_docs_base() / "Smart Citizen"


def resolve_dataforge_cache_base() -> Path:
    """Mirrors AppSettings.get_dataforge_cache_base()'s resolution order (registry mode)."""
    app_key = f"Software\\{ORG_NAME}\\{APP_NAME}"
    override = _read_reg_value(app_key, CACHE_DIR_KEY)
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app_data) / "Smart Citizen"


def is_smart_citizen_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except Exception:
        return False
    return any(name.lower() in out.lower() for name in PROCESS_NAMES)


def gather_targets() -> list[dict]:
    """Return the full list of deletion targets, each resolved up front so the
    confirmation prompt shows exactly what will be touched."""
    targets = []

    targets.append({
        "kind": "registry",
        "label": "Registry settings",
        "path": f"Software\\{ORG_NAME}\\{APP_NAME}",
        "display": f"HKCU\\Software\\{ORG_NAME}\\{APP_NAME}",
    })
    targets.append({
        "kind": "registry",
        "label": "Legacy registry settings (pre-0.9.0)",
        "path": f"Software\\{ORG_NAME}\\{LEGACY_APP_NAME}",
        "display": f"HKCU\\Software\\{ORG_NAME}\\{LEGACY_APP_NAME}",
    })

    user_data_dir = resolve_user_data_dir()
    targets.append({
        "kind": "folder",
        "label": "User data (user.ini, cache, backups, logs)",
        "path": user_data_dir,
        "display": str(user_data_dir),
    })

    legacy_docs_dir = _resolve_docs_base() / LEGACY_APP_NAME
    targets.append({
        "kind": "folder",
        "label": "Legacy user data (pre-0.9.0 rebrand)",
        "path": legacy_docs_dir,
        "display": str(legacy_docs_dir),
    })

    cache_base = resolve_dataforge_cache_base()
    targets.append({
        "kind": "folder",
        "label": "DataForge XML cache",
        "path": cache_base,
        "display": str(cache_base),
    })

    temp_dir = Path(os.environ.get("TEMP", "")) / "SmartCitizen-Update"
    targets.append({
        "kind": "folder",
        "label": "Leftover auto-updater downloads",
        "path": temp_dir,
        "display": str(temp_dir),
    })

    return targets


def target_exists(target: dict) -> bool:
    if target["kind"] == "registry":
        return _reg_key_exists(target["path"])
    return Path(target["path"]).exists()


def delete_target(target: dict) -> "tuple[bool, str]":
    """Returns (success, message)."""
    try:
        if target["kind"] == "registry":
            existed = _delete_reg_tree(target["path"])
            return (True, "deleted" if existed else "not present")
        else:
            path = Path(target["path"])
            if not path.exists():
                return (True, "not present")
            shutil.rmtree(path)
            return (True, "deleted")
    except PermissionError as e:
        return (False, f"permission denied ({e})")
    except OSError as e:
        return (False, f"failed ({e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset Smart Citizen to a fresh-install state.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted, delete nothing."
    )
    parser.add_argument(
        "--no-pause", action="store_true", help="Don't wait for a keypress before exiting."
    )
    args = parser.parse_args()

    print("=" * 64)
    print("  Smart Citizen Reset Tool")
    print("  Removes settings and user data left behind by the")
    print("  Windows uninstaller so the next install starts fresh.")
    print("=" * 64)
    print()

    if is_smart_citizen_running():
        print("Smart Citizen appears to be running. Close it first, then run this")
        print("tool again. Deleting files it has open can leave partial state.")
        if not args.yes:
            return _finish(1, args)

    targets = gather_targets()
    present = [t for t in targets if target_exists(t)]

    if not present:
        print("Nothing found to remove. Smart Citizen is already clean.")
        return _finish(0, args)

    print("The following will be permanently deleted:\n")
    for t in present:
        print(f"  [{t['kind']:8}] {t['label']}")
        print(f"             {t['display']}")
    print()

    if args.dry_run:
        print("(dry run, nothing was deleted)")
        return _finish(0, args)

    if not args.yes:
        answer = input("Type YES to permanently delete all of the above: ").strip()
        if answer != "YES":
            print("Cancelled. Nothing was deleted.")
            return _finish(1, args)

    print()
    failures = 0
    for t in present:
        ok, message = delete_target(t)
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {t['label']}: {message}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"Done, with {failures} item(s) that could not be removed (see above).")
        print("Common cause: a file is still open in another program, or you need")
        print("to run this tool as Administrator.")
        return _finish(1, args)

    print("Done. Smart Citizen is fully reset, the next launch starts clean.")
    return _finish(0, args)


def _finish(code: int, args) -> int:
    if not args.no_pause:
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())

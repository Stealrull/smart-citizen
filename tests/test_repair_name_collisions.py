"""Tests for scripts/repair_tool/repair_name_collisions.py (2.2.0 hotfix).

The standalone repair tool re-runs the FIXED merge pipeline against a
user's existing cached base.ini/user.ini/settings and rewrites their
already-applied global.ini, correcting any keys the old unscoped
sync_key_variants bug (#255/#257) touched. These tests reproduce the
exact reported shape (Stanton2/Stanton_2) plus a second unrelated
collision pair, using an isolated JsonSettings backend so nothing here
ever touches the real registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "repair_tool"))

from src.utils.json_settings import JsonSettings  # noqa: E402
from src.utils.settings import AppSettings  # noqa: E402

import repair_name_collisions as repair  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Isolated JsonSettings backend + a fake SC install/user-data layout."""
    saved_backend = AppSettings._backend
    AppSettings._backend = JsonSettings(tmp_path / "config.json")

    sc_root = tmp_path / "StarCitizen"
    (sc_root / "LIVE").mkdir(parents=True)
    (sc_root / "LIVE" / "Data.p4k").write_bytes(b"")

    user_data_dir = tmp_path / "SmartCitizenData"
    user_data_dir.mkdir()

    AppSettings.set_sc_install_root(str(sc_root))
    AppSettings.set_active_channel("LIVE")
    AppSettings.set_user_data_dir(str(user_data_dir))
    # Mirrors what the real Extract flow configures: the "global" source
    # path points at the cached base.ini (SOURCE_GLOBAL is enabled by default).
    AppSettings.set_source_path(AppSettings.SOURCE_GLOBAL, str(AppSettings.get_base_ini_path()))

    yield {"sc_root": sc_root, "user_data_dir": user_data_dir}

    AppSettings._backend = saved_backend


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestRepairChannel:
    def test_repairs_reported_stanton_collision(self, isolated_settings, capsys, monkeypatch):
        """The exact reported bug: Stanton2 (Crusader) got overwritten by
        Stanton_2's ("Stanton (Star)") value in the applied global.ini."""
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\nOtherKey=Unrelated\n")

        global_ini = AppSettings.get_global_ini_path()
        # Simulate the pre-fix corruption: both variants show the star's value.
        _write(global_ini, "Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\nOtherKey=Unrelated\n")

        repair._repair_channel("LIVE", auto_yes=True)

        result = dict(
            line.split("=", 1)
            for line in global_ini.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line
        )
        assert result["Stanton2"] == "Crusader"
        assert result["Stanton_2"] == "Stanton (Star)"
        assert result["OtherKey"] == "Unrelated"

    def test_repairs_second_unrelated_collision_in_same_run(self, isolated_settings):
        """A second, unrelated collision pair (kiosk label) in the same
        table must also be caught and fixed in the same pass."""
        base_ini = AppSettings.get_base_ini_path()
        _write(
            base_ini,
            "Stanton2=Crusader\nStanton_2=Stanton (Star)\n"
            "kiosk_ShopTerminal=Shop Terminal\nkiosk_Shop_Terminal=Shop_Terminal\n",
        )
        global_ini = AppSettings.get_global_ini_path()
        _write(
            global_ini,
            "Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\n"
            "kiosk_ShopTerminal=Shop Terminal\nkiosk_Shop_Terminal=Shop Terminal\n",
        )

        repair._repair_channel("LIVE", auto_yes=True)

        result = dict(
            line.split("=", 1)
            for line in global_ini.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line
        )
        assert result["Stanton2"] == "Crusader"
        assert result["kiosk_Shop_Terminal"] == "Shop_Terminal"

    def test_never_touches_user_customized_key(self, isolated_settings):
        """A user's deliberate edit to one of the colliding keys must survive
        the repair untouched -- it's a real customization, not corruption."""
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")

        user_ini = AppSettings.get_user_ini_path()
        _write(user_ini, "Stanton2=My Custom Planet Name\n")

        global_ini = AppSettings.get_global_ini_path()
        _write(global_ini, "Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\n")

        repair._repair_channel("LIVE", auto_yes=True)

        result = dict(
            line.split("=", 1)
            for line in global_ini.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line
        )
        assert result["Stanton2"] == "My Custom Planet Name"
        assert result["Stanton_2"] == "Stanton (Star)"

    def test_backs_up_before_repairing(self, isolated_settings):
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        global_ini = AppSettings.get_global_ini_path()
        _write(global_ini, "Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\n")

        repair._repair_channel("LIVE", auto_yes=True)

        backups = list(AppSettings.get_backups_dir().glob("global.ini.bak_*"))
        assert len(backups) == 1
        assert "Stanton (Star)" in backups[0].read_text(encoding="utf-8")

    def test_writes_bom_as_a_side_effect(self, isolated_settings):
        """Bonus of reusing the fixed merge_ini_files: repaired files also
        get the UTF-8 BOM the game's own loader needs (#261)."""
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        global_ini = AppSettings.get_global_ini_path()
        global_ini.parent.mkdir(parents=True, exist_ok=True)
        # Written without a BOM, matching an old 2.2.0 apply.
        global_ini.write_bytes(b"Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\n")

        repair._repair_channel("LIVE", auto_yes=True)

        assert global_ini.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_noop_when_nothing_to_repair(self, isolated_settings, capsys):
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        global_ini = AppSettings.get_global_ini_path()
        # Already-correct file, e.g. produced by 2.3.0 or a prior repair run.
        _write(global_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")

        repair._repair_channel("LIVE", auto_yes=True)

        assert "Nothing to repair" in capsys.readouterr().out
        assert not list(AppSettings.get_backups_dir().glob("global.ini.bak_*"))

    def test_declines_without_confirmation(self, isolated_settings, monkeypatch):
        """Without --yes, a 'no' answer at the prompt must leave the file untouched."""
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        global_ini = AppSettings.get_global_ini_path()
        _write(global_ini, "Stanton2=Stanton (Star)\nStanton_2=Stanton (Star)\n")

        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        repair._repair_channel("LIVE", auto_yes=False)

        result = dict(
            line.split("=", 1)
            for line in global_ini.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        assert result["Stanton2"] == "Stanton (Star)"

    def test_skips_channel_with_no_applied_file(self, isolated_settings, capsys):
        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        # No global.ini written -- nothing has been applied yet for this channel.

        repair._repair_channel("LIVE", auto_yes=True)

        assert "nothing to repair" in capsys.readouterr().out.lower()


class TestDetectInstalledChannels:
    """Regression coverage: main() must never treat
    AppSettings.get_available_channels()'s "all channel names" UI-dropdown
    placeholder (returned when sc_install_root is unset) as real installs.
    Doing so previously made main() iterate all 5 channel names and, just
    by computing each one's cache path, silently create empty
    Documents\\Smart Citizen\\<channel>\\cache\\ folders on a machine where
    nothing was actually configured."""

    def test_only_channels_with_data_p4k_are_detected(self, tmp_path):
        root = tmp_path / "StarCitizen"
        (root / "LIVE").mkdir(parents=True)
        (root / "LIVE" / "Data.p4k").write_bytes(b"")
        (root / "PTU").mkdir(parents=True)  # no Data.p4k -- not a real install

        assert repair._detect_installed_channels(str(root)) == ["LIVE"]

    def test_empty_root_detects_nothing(self, tmp_path):
        root = tmp_path / "does_not_exist"
        assert repair._detect_installed_channels(str(root)) == []


class TestMainChannelSafety:
    """main()'s own guards -- separate from _repair_channel's, since these
    are exactly what regressed: main() must bail out before touching any
    per-channel path when sc_install_root isn't configured, rather than
    falling through to AppSettings.get_available_channels()'s placeholder
    list."""

    def test_unconfigured_root_creates_no_folders(self, isolated_settings, monkeypatch, capsys):
        # Simulate a machine where Smart Citizen's install path was never set.
        # Monkeypatched directly (not just set_sc_install_root("")) because
        # get_sc_install_root() has its own last-resort fallback that checks
        # hardcoded real filesystem paths regardless of settings backend --
        # on a dev machine that happens to have a stray/incomplete RSI
        # Launcher folder at one of those paths, clearing the setting alone
        # wouldn't reproduce "unconfigured" here.
        monkeypatch.setattr(AppSettings, "get_sc_install_root", staticmethod(lambda: ""))
        user_data_dir = isolated_settings["user_data_dir"]
        # isolated_settings' own setup already creates LIVE/cache/ (to
        # configure the "global" source path) -- snapshot that baseline so
        # the assertion below is about what main() itself adds, not fixture
        # setup that happens to predate it.
        before = {p for p in user_data_dir.rglob("*")}

        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--yes", "--no-pause"])
        exit_code = repair.main()

        assert exit_code == 1
        assert "could not find your star citizen install path" in capsys.readouterr().out.lower()
        # The bug this guards: computing a channel's cache path creates it as
        # a side effect. With no configured root, main() must not create or
        # touch anything beyond what was already there.
        after = {p for p in user_data_dir.rglob("*")}
        assert after == before

    def test_configured_root_only_processes_real_channels(self, isolated_settings, monkeypatch, capsys):
        sc_root = isolated_settings["sc_root"]
        user_data_dir = isolated_settings["user_data_dir"]
        # PTU has no Data.p4k -- must never be touched.
        (sc_root / "PTU").mkdir(parents=True)

        base_ini = AppSettings.get_base_ini_path()
        _write(base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")

        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--yes", "--no-pause"])
        exit_code = repair.main()

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Channels to check: LIVE" in out
        assert not (user_data_dir / "PTU").exists()


class TestPortableModeFallback:
    """Regression coverage: a PORTABLE Smart Citizen build keeps its
    settings in a JSON file next to its own exe, not the Windows registry
    at all -- a tester hit exactly this, running a portable build while
    the tool only ever looked at the registry. main() must fall back to
    asking for (or accepting via --data-dir) the portable install's data
    folder instead of just reporting nothing found."""

    def test_resolve_config_path_from_exe_file(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "config.json").write_text("{}", encoding="utf-8")
        exe_path = tmp_path / "SmartCitizen-Portable-v2.2.0.exe"
        exe_path.write_bytes(b"")

        assert repair.resolve_portable_config_path(str(exe_path)) == data_dir / "config.json"

    def test_resolve_config_path_from_portable_folder(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "config.json").write_text("{}", encoding="utf-8")

        assert repair.resolve_portable_config_path(str(tmp_path)) == data_dir / "config.json"

    def test_resolve_config_path_from_data_folder_itself(self, tmp_path):
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        assert repair.resolve_portable_config_path(str(tmp_path)) == tmp_path / "config.json"

    def test_resolve_config_path_not_found(self, tmp_path):
        assert repair.resolve_portable_config_path(str(tmp_path / "nowhere")) is None

    def test_resolve_config_path_empty_input(self):
        assert repair.resolve_portable_config_path("") is None
        assert repair.resolve_portable_config_path("   ") is None

    def _write_portable_config(self, data_dir: Path, sc_root: Path) -> Path:
        """Write a real config.json the way the portable app itself would."""
        from src.utils.json_settings import JsonSettings

        saved = AppSettings._backend
        AppSettings._backend = JsonSettings(data_dir / "config.json")
        AppSettings.set_sc_install_root(str(sc_root))
        AppSettings.set_active_channel("LIVE")
        AppSettings._backend = saved
        return data_dir / "config.json"

    def _fake_registry_then_real(self, monkeypatch, registry_backend):
        """Patch get_sc_install_root() to look unconfigured only while the
        original (registry-like) backend is active, and behave normally
        again once main() swaps in the portable backend -- this avoids
        depending on whether this machine happens to have a real install at
        get_sc_install_root()'s own hardcoded last-resort fallback paths."""
        real_get_root = AppSettings.get_sc_install_root

        def fake():
            if AppSettings._backend is registry_backend:
                return ""
            return real_get_root()

        monkeypatch.setattr(AppSettings, "get_sc_install_root", staticmethod(fake))

    def test_data_dir_flag_finds_portable_install(self, isolated_settings, monkeypatch, tmp_path, capsys):
        self._fake_registry_then_real(monkeypatch, AppSettings._backend)

        portable_data = tmp_path / "PortableInstall" / "data"
        portable_data.mkdir(parents=True)
        portable_sc_root = tmp_path / "PortableGame"
        (portable_sc_root / "LIVE").mkdir(parents=True)
        (portable_sc_root / "LIVE" / "Data.p4k").write_bytes(b"")
        self._write_portable_config(portable_data, portable_sc_root)

        monkeypatch.setattr(sys, "argv", [
            "repair_name_collisions.py", "--yes", "--no-pause",
            "--data-dir", str(portable_data),
        ])
        exit_code = repair.main()

        out = capsys.readouterr().out
        assert "Using portable settings" in out
        assert f"Star Citizen install path: {portable_sc_root}" in out
        assert "Channels to check: LIVE" in out
        assert exit_code == 0

    def test_interactive_prompt_finds_portable_install(self, isolated_settings, monkeypatch, tmp_path, capsys):
        self._fake_registry_then_real(monkeypatch, AppSettings._backend)
        # Deterministic regardless of what's actually on this machine's
        # Desktop/Downloads/tool-directory -- this test is specifically
        # about the manual-prompt fallback, not auto-discovery.
        monkeypatch.setattr(repair, "find_portable_data_dirs", lambda: [])

        portable_data = tmp_path / "PortableInstall" / "data"
        portable_data.mkdir(parents=True)
        portable_sc_root = tmp_path / "PortableGame"
        (portable_sc_root / "LIVE").mkdir(parents=True)
        (portable_sc_root / "LIVE" / "Data.p4k").write_bytes(b"")
        self._write_portable_config(portable_data, portable_sc_root)

        # No --yes here: that flag means "fully non-interactive", which
        # (by design) also skips the portable-path prompt this test exists
        # to exercise. No base.ini exists for this fake install, so
        # _repair_channel returns before it would need a second input().
        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--no-pause"])
        monkeypatch.setattr("builtins.input", lambda _prompt: str(portable_data))
        exit_code = repair.main()

        out = capsys.readouterr().out
        assert "Using portable settings" in out
        assert "Channels to check: LIVE" in out
        assert exit_code == 0

    def test_skipping_the_prompt_falls_through_to_error(self, isolated_settings, monkeypatch, capsys):
        self._fake_registry_then_real(monkeypatch, AppSettings._backend)
        monkeypatch.setattr(repair, "find_portable_data_dirs", lambda: [])

        # No --yes: this test is specifically about the user seeing the
        # portable-path prompt and pressing Enter (empty answer) to skip it.
        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--no-pause"])
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        exit_code = repair.main()

        assert exit_code == 1
        assert "could not find your star citizen install path" in capsys.readouterr().out.lower()

    def test_yes_flag_skips_prompt_entirely(self, isolated_settings, monkeypatch, capsys):
        """--yes means fully non-interactive -- it must never block on the
        portable-path prompt either, even though nothing was auto-detected."""
        self._fake_registry_then_real(monkeypatch, AppSettings._backend)
        monkeypatch.setattr(repair, "find_portable_data_dirs", lambda: [])

        def _unexpected_input(_prompt):
            raise AssertionError("input() must not be called when --yes is set")

        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--yes", "--no-pause"])
        monkeypatch.setattr("builtins.input", _unexpected_input)
        exit_code = repair.main()

        assert exit_code == 1
        assert "could not find your star citizen install path" in capsys.readouterr().out.lower()


class TestFindPortableDataDirs:
    """Zero-input auto-discovery: the tool must find a portable install on
    its own for the layouts a user is actually likely to have, without
    ever asking them to type or paste anything. Path.home() is
    monkeypatched to an empty tmp_path throughout so these never depend on
    what's actually sitting in this machine's real Desktop/Downloads."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

    def _make_portable_install(self, folder: Path) -> Path:
        folder.mkdir(parents=True)
        (folder / "SmartCitizen-Portable-v2.2.0.exe").write_bytes(b"")
        data_dir = folder / "data"
        data_dir.mkdir()
        (data_dir / "config.json").write_text("{}", encoding="utf-8")
        return data_dir / "config.json"

    def test_finds_sibling_folder(self, tmp_path):
        """Tool dropped in a folder next to the portable install (the
        tester's actual layout: repair tool in .../tester/, portable
        install in a sibling .../SmartCitizen-Portable-v2.2.0/)."""
        parent = tmp_path / "Games"
        tool_dir = parent / "tester"
        tool_dir.mkdir(parents=True)
        expected = self._make_portable_install(parent / "SmartCitizen-Portable-v2.2.0")

        assert repair.find_portable_data_dirs(start_dir=tool_dir) == [expected]

    def test_finds_install_containing_the_tool_itself(self, tmp_path):
        """Tool dropped directly inside the portable install's own folder,
        alongside its exe."""
        portable_dir = tmp_path / "SmartCitizen-Portable-v2.2.0"
        expected = self._make_portable_install(portable_dir)

        assert repair.find_portable_data_dirs(start_dir=portable_dir) == [expected]

    def test_finds_install_one_level_above_the_tool(self, tmp_path):
        """Tool dropped inside a subfolder of wherever the portable install
        sits (e.g. a 'tools' subfolder next to the portable exe)."""
        portable_dir = tmp_path / "SmartCitizen-Portable-v2.2.0"
        tool_dir = portable_dir / "tools"
        tool_dir.mkdir(parents=True)
        (portable_dir / "SmartCitizen-Portable-v2.2.0.exe").write_bytes(b"")
        data_dir = portable_dir / "data"
        data_dir.mkdir()
        (data_dir / "config.json").write_text("{}", encoding="utf-8")

        assert repair.find_portable_data_dirs(start_dir=tool_dir) == [data_dir / "config.json"]

    def test_finds_install_on_desktop(self, tmp_path):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir(exist_ok=True)
        tool_dir = tmp_path / "SomewhereElse"
        tool_dir.mkdir()
        expected = self._make_portable_install(fake_home / "Desktop" / "SmartCitizen-Portable-v2.2.0")

        assert repair.find_portable_data_dirs(start_dir=tool_dir) == [expected]

    def test_picks_most_recently_modified_when_multiple_found(self, tmp_path, monkeypatch):
        parent = tmp_path / "Games"
        tool_dir = parent / "tester"
        tool_dir.mkdir(parents=True)
        older = self._make_portable_install(parent / "SmartCitizen-Portable-v2.1.0")
        newer = self._make_portable_install(parent / "SmartCitizen-Portable-v2.2.0")

        import os
        import time
        older_time = time.time() - 1000
        os.utime(older, (older_time, older_time))

        found = repair.find_portable_data_dirs(start_dir=tool_dir)
        assert found[0] == newer
        assert set(found) == {older, newer}

    def test_finds_nothing_when_no_portable_install_nearby(self, tmp_path):
        tool_dir = tmp_path / "just_a_folder"
        tool_dir.mkdir()
        assert repair.find_portable_data_dirs(start_dir=tool_dir) == []


class TestMainAutoDiscoveryNoInput:
    """The end-to-end 'stupid proof' path: registry mode finds nothing,
    but an auto-discoverable portable install is sitting right there --
    main() must use it without ever calling input()."""

    def test_auto_discovers_with_zero_prompts(self, isolated_settings, monkeypatch, tmp_path, capsys):
        real_get_root = AppSettings.get_sc_install_root
        registry_backend = AppSettings._backend

        def fake_get_root():
            if AppSettings._backend is registry_backend:
                return ""
            return real_get_root()

        monkeypatch.setattr(AppSettings, "get_sc_install_root", staticmethod(fake_get_root))

        portable_dir = tmp_path / "SmartCitizen-Portable-v2.2.0"
        portable_dir.mkdir()
        (portable_dir / "SmartCitizen-Portable-v2.2.0.exe").write_bytes(b"")
        portable_data = portable_dir / "data"
        portable_data.mkdir()
        portable_sc_root = tmp_path / "PortableGame"
        (portable_sc_root / "LIVE").mkdir(parents=True)
        (portable_sc_root / "LIVE" / "Data.p4k").write_bytes(b"")

        from src.utils.json_settings import JsonSettings
        saved = AppSettings._backend
        AppSettings._backend = JsonSettings(portable_data / "config.json")
        AppSettings.set_sc_install_root(str(portable_sc_root))
        AppSettings.set_active_channel("LIVE")
        AppSettings._backend = saved

        monkeypatch.setattr(repair, "_this_tools_own_dir", lambda: tmp_path / "tester")
        (tmp_path / "tester").mkdir()

        def _unexpected_input(_prompt):
            raise AssertionError("input() must not be called -- this is the zero-prompt auto-discovery path")

        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--yes", "--no-pause"])
        monkeypatch.setattr("builtins.input", _unexpected_input)
        exit_code = repair.main()

        out = capsys.readouterr().out
        assert "Found a portable Smart Citizen install" in out
        assert "Channels to check: LIVE" in out
        assert exit_code == 0

    def test_falls_back_to_portable_when_registry_channels_have_no_cache(
        self, isolated_settings, monkeypatch, tmp_path, capsys,
    ):
        """The exact bug a tester hit: sc_install_root correctly resolves
        to a real game install (Data.p4k genuinely present), so channels
        ARE found -- but a stale/leftover user_data_dir override means
        none of those channels have ever had anything cached there. That
        must still trigger the portable fallback, not silently print
        "no cached base.ini" against the wrong location forever."""
        # isolated_settings already gives a valid sc_install_root + LIVE
        # channel with Data.p4k, but deliberately never writes base.ini --
        # i.e. exactly "found the game, nothing cached where we looked".

        portable_dir = tmp_path / "SmartCitizen-Portable-v2.2.0"
        portable_dir.mkdir()
        (portable_dir / "SmartCitizen-Portable-v2.2.0.exe").write_bytes(b"")
        portable_data = portable_dir / "data"
        portable_data.mkdir()
        portable_sc_root = tmp_path / "PortableGame"
        (portable_sc_root / "LIVE").mkdir(parents=True)
        (portable_sc_root / "LIVE" / "Data.p4k").write_bytes(b"")

        from src.utils.json_settings import JsonSettings
        saved = AppSettings._backend
        AppSettings._backend = JsonSettings(portable_data / "config.json")
        AppSettings.set_sc_install_root(str(portable_sc_root))
        AppSettings.set_active_channel("LIVE")
        portable_base_ini = AppSettings.get_base_ini_path()
        _write(portable_base_ini, "Stanton2=Crusader\nStanton_2=Stanton (Star)\n")
        AppSettings._backend = saved

        monkeypatch.setattr(repair, "_this_tools_own_dir", lambda: tmp_path / "tester")
        (tmp_path / "tester").mkdir()

        monkeypatch.setattr(sys, "argv", ["repair_name_collisions.py", "--yes", "--no-pause"])
        exit_code = repair.main()

        out = capsys.readouterr().out
        assert "Found a portable Smart Citizen install" in out
        assert f"Star Citizen install path: {portable_sc_root}" in out
        assert exit_code == 0

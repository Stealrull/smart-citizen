"""Build script for the standalone Blueprint Scan reset tool.

Usage:
    python scripts/blueprint_rescan_tool/build_reset_tool_exe.py

Produces a single console EXE at dist/SmartCitizen-BlueprintScanReset.exe.
Deliberately does not reuse scripts/build/build_exe.py: that one bundles the
full GUI (assets, languages, docs, the enhancements generator) for the main
app. This tool only needs QtCore (via AppSettings' QSettings calls) and
src.utils -- no QtWidgets, no bundled docs or language files -- so a
separate, much smaller build keeps it that way. Mirrors
scripts/repair_tool/build_repair_exe.py's shape.
"""
import os
import shutil
import sys

import PyInstaller.__main__

project_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(project_dir))
# reset_blueprint_scan.py imports `from src.utils...` (repo-root-relative,
# same convention as src/main.py), so the repo root -- not root_dir/src --
# must be importable for PyInstaller's analysis to resolve the "src" package.
sys.path.insert(0, root_dir)

entry_point = os.path.join(project_dir, 'reset_blueprint_scan.py')
version_file = os.path.join(root_dir, 'VERSION.TXT')

# Own build cache dir, deliberately NOT root_dir/build -- that one is shared
# with scripts/build/build_exe.py, whose --portable runs temporarily write
# src/utils/_build_info.py (IS_PORTABLE = True) before cleaning it up. A
# stale PyInstaller Analysis cache under a shared workpath can carry that
# module's bundled bytecode into an unrelated build even after the source
# file is gone, silently flipping this tool into portable mode (a local
# JSON settings file instead of the real Windows registry) with no error.
# Cleaned before every build so there's never a stale cache to inherit.
work_dir = os.path.join(root_dir, 'build', 'blueprint_rescan_tool')
if os.path.exists(work_dir):
    shutil.rmtree(work_dir)

args = [
    entry_point,
    '--name', 'SmartCitizen-BlueprintScanReset',
    '--onefile',
    '--console',
    '--paths', root_dir,
    '--add-data', f'{version_file}{os.pathsep}.',
    '--workpath', work_dir,
    '--specpath', work_dir,
    '--distpath', os.path.join(root_dir, 'dist'),
    '--hidden-import=src.utils',
]

print("Building SmartCitizen-BlueprintScanReset.exe...")
PyInstaller.__main__.run(args)
print("\nDone: dist/SmartCitizen-BlueprintScanReset.exe")

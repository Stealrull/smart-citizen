"""Build script for reset_smart_citizen.exe.

Usage:
    python build_reset_tool_exe.py
"""
import os
import shutil

import PyInstaller.__main__

project_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(project_dir))

script_path = os.path.join(project_dir, "reset_smart_citizen.py")
work_dir = os.path.join(root_dir, "build", "reset_tool")
dist_dir = os.path.join(root_dir, "dist", "reset_tool")

if os.path.isdir(work_dir):
    shutil.rmtree(work_dir)

print("Building reset_smart_citizen.exe ...")

PyInstaller.__main__.run([
    script_path,
    "--name=reset_smart_citizen",
    "--onefile",
    "--console",
    f"--distpath={dist_dir}",
    f"--workpath={work_dir}",
    f"--specpath={work_dir}",
    "--clean",
    "--noconfirm",
])

exe_path = os.path.join(dist_dir, "reset_smart_citizen.exe")
if os.path.exists(exe_path):
    print(f"\nBuilt: {exe_path}")
else:
    raise SystemExit("Build finished but reset_smart_citizen.exe was not found — check the log above.")

"""Adds an optional switch for the wall function used for nut:   NUT_WALL=nutUSpaldingWallFunction  (default stays nutkWallFunction)
Usage (from the repo root):  python tools/apply_nut_wall_option.py [backend/pipeline.py]
Makes a backup (pipeline.py.bak), changes only the wall-function literal in write_case, and records the choice in the report's solver line when possible."""
import os, shutil, sys
p = sys.argv[1] if len(sys.argv) > 1 else "backend/pipeline.py"; t = open(p).read()
if "NUT_WALL" in t: print("already patched"); sys.exit(0)
old = '"type nutkWallFunction; value uniform 0;"'
if t.count(old) != 1: print(f"expected the nut wall literal exactly once, found {t.count(old)}; nothing changed. Send me the nut line of write_case."); sys.exit(1)
shutil.copy(p, p + ".bak")
t = t.replace(old, '"type " + os.environ.get("NUT_WALL", "nutkWallFunction") + "; value uniform 0;"')
old2 = 'solver=f"simpleFoam kOmegaSST ({IMAGE})"'
if t.count(old2) == 1: t = t.replace(old2, 'solver=f"simpleFoam kOmegaSST, nut wall {os.environ.get(\'NUT_WALL\', \'nutkWallFunction\')} ({IMAGE})"')
else: print("note: report solver line not found; the choice will not appear in the report (it is still applied)")
open(p, "w").write(t); print("patched; backup at", p + ".bak")

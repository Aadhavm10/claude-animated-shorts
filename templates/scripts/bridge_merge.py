# Merge the four look sandboxes back into the main bridge folder, by owned files.
# Backs up main first (backup/bridge_<time>/), copies each sandbox's owned files, reports which owned files are
# missing or unchanged. Run from the Remotion project root.
# Usage: python scripts/bridge_merge.py [cam,world,mat,char,post] [name]   (name = src/videos/<name>, default bridge)
import os, shutil, sys, time, filecmp

ROOT = os.getcwd()
NAME = sys.argv[2] if len(sys.argv) > 2 else "bridge"
MAIN = os.path.join(ROOT, "src/videos", NAME)
OWN = {  # round 2 ownership (round 1: world also owned paint/build, post owned grade)
    "cam": ["story.ts"],
    "world": ["world.ts", "lights.ts", "shade.ts", "grade.ts"],
    "mat": ["paint.ts", "build.ts"],
    "char": ["toon.ts", "cast.ts", "cardshade.ts"],
    "post": ["ink.ts", "Bridge.tsx", "lensblur.ts"],
}
which = sys.argv[1].split(",") if len(sys.argv) > 1 else list(OWN)
bak = os.path.join(ROOT, "backup", time.strftime(f"{NAME}_%Y%m%d_%H%M%S"))
shutil.copytree(MAIN, bak)
print("backup ->", bak)
for box in which:
    src = os.path.join(ROOT, f"src/videos/{NAME}_{box}")
    for f in OWN[box]:
        a, b = os.path.join(src, f), os.path.join(MAIN, f)
        if not os.path.exists(a):
            print(f"  {box}: {f} not in sandbox (skipped)")
            continue
        if os.path.exists(b) and filecmp.cmp(a, b, shallow=False):
            print(f"  {box}: {f} unchanged")
            continue
        shutil.copy2(a, b)
        print(f"  {box}: {f} -> main")

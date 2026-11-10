# Re-voice a FINISHED short without re-authoring it: warp output time onto "story time" (the narration the picture and
# sound were built on). One piecewise-linear warp, anchored on the start of every word the picture or the music hits
# (timeline.W + EVENTS) plus every sentence start, is written to:
#   src/videos/<name>/retime.ts   (picture: toStory(outputFrame) -> fractional story frame; toOutput() the inverse)
#   <short>/audio/timeline.py     (sound: RETIME / DUR_NEW / M(story s) -> output s; appended on first run)
# Wiring (once per short, see SKILL.md "Re-voicing a finished short"): <Name>.tsx evaluates the scene at toStory(frame) and
# exports NEW_TOTAL as the duration; the white flash and kickAt() count in output frames; music Band.add / sfx render
# place at T.M(t); bed lengths = M(b) - M(a); mix.py maps SECTIONS / FLASH / REPORT through M and uses DUR_NEW.
# Usage:  python3 scripts/make_retime.py <short dir> <src/videos/name dir> <story total frames> "<note>" [events.json]
#   <short>/voice_story/words.json = the old take, <short>/voice/words.json = the new take (same script; run
#   pad_pauses.py first if the new voice barely pauses). events.json = story frames of every picture event / cut / key.
import json, os, re, sys

# Walkie-Talkie example of an events list (story frames of every picture event / cut / camera key):
WALKIE_EVENTS = [18, 46, 94, 146, 151, 172, 177, 214, 226, 232, 240, 256, 312, 316, 352, 396, 404, 406, 440, 450, 453, 494, 504, 506,
          510, 553, 560, 621, 624, 647, 699, 737, 742, 776, 806, 868, 874, 900, 934, 948, 994, 1000, 1078, 1088, 1140, 1150,
          1158, 1181, 1205, 1209, 1212, 1240, 1262, 1272, 1286, 1290, 1296, 1300, 1334, 1338, 1397, 1447, 1480, 1482, 1483,
          1505, 1534, 1556, 1584, 1610, 1622, 1626, 1635, 1661, 1684, 1704, 1708, 1748, 1795, 1838, 1840, 1846, 1864, 1870,
          1946, 1958, 1972]


TS_TEMPLATE = """// Re-timing to the current narration (NOTE). The film stays authored in story time (story.ts frames); this warp,
// anchored on every word the picture or the music hits, maps output time to story time. Regenerate with make_retime.py.
export const RETIME: [number, number][] = []; // [story s, output s]
export const NEW_TOTAL = 0;
const FPS = 30;
const interp = (x: number, from: 0 | 1) => {
  const to = 1 - from, A = RETIME;
  if (x <= A[0][from]) return A[0][to];
  for (let i = 1; i < A.length; i++) if (x <= A[i][from]) {
    const a = A[i - 1], b = A[i];
    return a[to] + ((b[to] - a[to]) * (x - a[from])) / (b[from] - a[from]);
  }
  return A[A.length - 1][to];
};
/** output frame -> story frame (fractional) */
export const toStory = (frame: number) => interp(frame / FPS, 1) * FPS;
/** story frame -> output frame (fractional) */
export const toOutput = (f: number) => interp(f / FPS, 0) * FPS;
"""
PY_TAIL = """

# ---------------- re-timing to the current narration (NOTE) ----------------
# Everything above stays in story time. M() maps story seconds -> output seconds (same warp as retime.ts).
RETIME = []
DUR_NEW = 0 / FPS
STORY_DUR = DUR


def M(t):
    import numpy as _np
    return float(_np.interp(t, [a for a, _ in RETIME], [b for _, b in RETIME]))
"""


def main(short, vdir, STORY_TOTAL, note, events_path=None):
    HERE = os.path.join(short, "audio")
    sys.path.insert(0, HERE)
    import timeline as T
    TS = os.path.join(vdir, "retime.ts")
    EVENTS = json.load(open(events_path)) if events_path else []
    if not os.path.exists(TS):
        open(TS, "w").write(TS_TEMPLATE)
    if "def M(t)" not in open(os.path.join(HERE, "timeline.py")).read():
        open(os.path.join(HERE, "timeline.py"), "a").write(PY_TAIL)
    o = json.load(open(os.path.join(short, "voice_story", "words.json")))
    n = json.load(open(os.path.join(short, "voice", "words.json")))
    assert len(o) == len(n) and all(a["w"] == b["w"] for a, b in zip(o, n)), "the new narration must be the same script"
    near = lambda t: min(range(len(o)), key=lambda i: abs(o[i]["s"] - t))
    idx = {near(v) for v in getattr(T, "W", {}).values()}
    idx |= {near(f / 30) for f in EVENTS if abs(o[near(f / 30)]["s"] - f / 30) < 0.35}
    idx |= {i for i in range(len(o)) if i == 0 or o[i - 1]["w"][-1] in ".?!"}
    tail = STORY_TOTAL / 30 - o[-1]["e"]
    frames = round((n[-1]["e"] + tail) * 30)
    A = [(0.0, 0.0)] + [(round(o[i]["s"], 3), round(n[i]["s"], 3)) for i in sorted(idx) if o[i]["s"] > 0] + [(STORY_TOTAL / 30, round(frames / 30, 4))]
    sl = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(A, A[1:])]
    print(f"{len(A)} anchors, output {frames} frames ({frames / 30:.2f} s), local speed {min(sl):.2f}..{max(sl):.2f}")
    pairs = ", ".join(f"[{a}, {b}]" for a, b in A)
    ts = open(TS).read()
    ts = re.sub(r"export const RETIME: \[number, number\]\[\] = \[.*?\]; ", f"export const RETIME: [number, number][] = [{pairs}]; ", ts, flags=re.S)
    ts = re.sub(r"export const NEW_TOTAL = \d+;", f"export const NEW_TOTAL = {frames};", ts)
    ts = re.sub(r"^// Re-timing to the current narration \(.*?\)", f"// Re-timing to the current narration ({note})", ts, flags=re.M)
    open(TS, "w").write(ts)
    py = open(os.path.join(HERE, "timeline.py")).read()
    py = re.sub(r"^RETIME = \[.*\]$", f"RETIME = [{pairs}]", py, flags=re.M)
    py = re.sub(r"^DUR_NEW = \d+ / FPS$", f"DUR_NEW = {frames} / FPS", py, flags=re.M)
    py = re.sub(r"^# ---------------- re-timing to the current narration \(.*?\)", f"# ---------------- re-timing to the current narration ({note})", py, flags=re.M)
    open(os.path.join(HERE, "timeline.py"), "w").write(py)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit("usage: make_retime.py <short dir> <src/videos/name dir> <story total frames> <note> [events.json]")
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)

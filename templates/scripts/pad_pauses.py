# Re-space a new narration's pauses toward the first narration's rhythm, without touching the words.
# Some voices barely pause at full stops / commas; warping the picture onto them makes the camera lurch 2-3x at every
# sentence end. This inserts silence in the middle of each punctuation gap that is shorter than PAUSE_K x the story
# narration's gap (the story words.json argument), and shifts words.json to match. The raw take is kept as voice_raw.wav /
# words_raw.json (re-running starts from those, so it is idempotent).
# Usage:  python3 scripts/pad_pauses.py <short>/voice <short>/voice_story/words.json
#   <short>/voice            the new take (voice.wav + words.json, same script)
#   voice_story/words.json   the narration the picture was built on ("story time")
import json, os, shutil, sys, wave
import numpy as np



def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    assert sw == 3, "expects the 24-bit voice.wav from generate_voice.py"
    b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / float(1 << 23)
    return x.reshape(-1, ch).T, sr

PAUSE_K = 0.85      # target gap = at least this share of the story narration's gap
MAX_ADD = 0.6       # never add more than this per gap (s)
FADE = 0.004        # crossfade into / out of the inserted silence (s)


def main(V, story_words):
    raw_wav, raw_js = os.path.join(V, "voice_raw.wav"), os.path.join(V, "words_raw.json")
    if not os.path.exists(raw_wav):
        shutil.copy(os.path.join(V, "voice.wav"), raw_wav)
        shutil.copy(os.path.join(V, "words.json"), raw_js)
    x, sr = read_wav(raw_wav)
    x = x[0]
    n = json.load(open(raw_js))
    o = json.load(open(story_words))
    assert len(o) == len(n) and all(a["w"] == b["w"] for a, b in zip(o, n))
    inserts = []  # (time in raw take, seconds of silence)
    for i in range(len(n) - 1):
        if n[i]["w"][-1] not in ".?!,;:":
            continue
        g_old, g_new = o[i + 1]["s"] - o[i]["e"], n[i + 1]["s"] - n[i]["e"]
        add = min(MAX_ADD, PAUSE_K * g_old - g_new)
        if add > 0.02:
            inserts.append(((n[i]["e"] + n[i + 1]["s"]) / 2, add))
    parts, last, shift = [], 0, {}
    total = 0.0
    for t, add in inserts:
        k = int(round(t * sr))
        seg = x[last:k].copy()
        f = int(FADE * sr)
        seg[-f:] *= np.linspace(1, 0, f)
        parts.append(seg)
        parts.append(np.zeros(int(round(add * sr))))
        nxt = x[k:k + f].copy()
        x[k:k + f] = nxt * np.linspace(0, 1, f)
        last = k
        total += add
        shift[t] = total
    parts.append(x[last:])
    y = np.concatenate(parts)
    out = []
    for w in n:
        d = sum(a for t, a in inserts if t < w["s"])
        out.append({"w": w["w"], "s": round(w["s"] + d, 3), "e": round(w["e"] + d, 3)})
    q = np.clip(np.round(y * 8388607.0), -8388608, 8388607).astype("<i4")
    with wave.open(os.path.join(V, "voice.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(3); w.setframerate(sr)
        w.writeframes(np.ascontiguousarray(q).view(np.uint8).reshape(-1, 4)[:, :3].tobytes())
    json.dump(out, open(os.path.join(V, "words.json"), "w"), indent=1)
    print(f"{len(inserts)} pauses lengthened by {total:.2f} s in all; narration {n[-1]['e']:.2f} -> {out[-1]['e']:.2f} s")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__ or "usage: pad_pauses.py <voice dir> <story words.json>")
    main(sys.argv[1], sys.argv[2])

#!/usr/bin/env python3
"""Generate voice.wav and words.json from a script using ElevenLabs TTS with timestamps.

Usage:
    python scripts/generate_voice.py <script_file> <output_dir>

    script_file: Path to SCRIPT.md or a plain-text narration file.
                 Preferred: wrap the spoken text in <!-- narration --> ... <!-- /narration -->.
                 Without markers, the leading header / `Key:` note paragraphs are skipped and narration
                 runs until the next markdown header or '---' (see extract_narration).
    output_dir:  Where to write voice.wav and words.json (e.g. ~/short-name/voice/).

Environment variables (or .env in the current folder, the repo root or the Remotion project root):
    ELEVENLABS_API_KEY  – required
    ELEVENLABS_VOICE_ID – optional (default: the ElevenLabs stock voice "Adam")
    ELEVENLABS_MODEL    – optional (default: eleven_multilingual_v2)
    ELEVENLABS_SPEED    – optional speaking speed 0.7-1.2 (default: 1.15)

Requirements: requests, python-dotenv (optional), ffmpeg on PATH.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REQUIRED_SR = 48000     # voice.wav sample rate expected by the audio pipeline (dsp.py)
REQUIRED_CHANNELS = 1   # mono (mix.py converts to stereo via dsp.st())
TARGET_DURATION = (55, 70)  # seconds — the ideal window

DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.65,
    "similarity_boost": 0.78,
    "style": 0.15,
    "use_speaker_boost": True,
    # The style is a fast, steady narrator (~3.2-3.7 words/s overall). At speed 1.0 ElevenLabs stock voices land
    # near 2.8 words/s (the 218-word bridge script ran 77 s instead of 67.5 s), so speed up by default.
    # ElevenLabs accepts 0.7-1.2; override with ELEVENLABS_SPEED.
    "speed": 1.15,
}

API_BASE = "https://api.elevenlabs.io"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_env():
    """Load the first .env found: the current folder, one or two levels up from this script (the skill repo
    root, or the Remotion project root when the script is copied to <project>/scripts/), then the installed skill."""
    try:
        from dotenv import load_dotenv
        here = os.path.dirname(os.path.abspath(__file__))
        for up in [
            os.getcwd(),
            os.path.join(here, "..", ".."),  # skill repo root (templates/scripts/ -> repo)
            os.path.join(here, ".."),        # Remotion project root (<project>/scripts/ -> project)
            os.path.expanduser("~/.claude/skills/cartoon-3d-short"),
        ]:
            env_path = os.path.join(os.path.abspath(up), ".env")
            if os.path.isfile(env_path):
                load_dotenv(env_path)
                return
    except ImportError:
        pass  # python-dotenv not installed; fall back to environment


NARRATION_OPEN = "<!-- narration -->"
NARRATION_CLOSE = "<!-- /narration -->"


def extract_narration(path: str) -> str:
    """Pull the spoken narration out of a SCRIPT.md or plain text file.

    1. If the file has `<!-- narration -->` ... `<!-- /narration -->` markers, exactly the text between them
       is spoken (the preferred SCRIPT.md layout; everything else in the file is notes).
    2. Otherwise the file is split into blank-line-separated paragraphs. Leading paragraphs that start with a
       markdown header ('#'), a `Key:` metadata line or YAML front matter ('---') are notes, including their
       continuation lines (e.g. a multi-line `Facts:` block). Narration is every paragraph after that, up to
       the next markdown header or '---' line (shot notes, appendices and so on are never spoken).
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    if NARRATION_OPEN in raw:
        i = raw.index(NARRATION_OPEN) + len(NARRATION_OPEN)
        j = raw.find(NARRATION_CLOSE, i)
        if j < 0:
            raise ValueError(f"{path}: '{NARRATION_OPEN}' has no matching '{NARRATION_CLOSE}'")
        text = "\n".join(l.strip() for l in raw[i:j].splitlines() if l.strip())
        if not text:
            raise ValueError(f"No narration text between the markers in {path}")
        return text

    # YAML front matter at the very top
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1:]

    paras: list[list[str]] = []
    cur: list[str] = []
    for line in lines + [""]:
        s = line.strip()
        if s:
            cur.append(s)
        elif cur:
            paras.append(cur)
            cur = []

    is_note = lambda p: p[0].startswith("#") or p[0] == "---" or re.match(r"^[A-Z][A-Za-z ]{0,30}:\s", p[0]) is not None
    k = 0
    while k < len(paras) and is_note(paras[k]):
        k += 1
    out: list[str] = []
    for p in paras[k:]:
        stop = next((n for n, s in enumerate(p) if s.startswith("#") or s == "---"), None)
        out.extend(p[:stop] if stop is not None else p)
        if stop is not None:
            break

    text = "\n".join(out).strip()
    if not text:
        raise ValueError(f"No narration text found in {path}")
    return text


def call_elevenlabs(text: str, api_key: str, voice_id: str, model: str) -> dict:
    """Call the ElevenLabs TTS-with-timestamps endpoint."""
    import requests

    url = f"{API_BASE}/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {**DEFAULT_VOICE_SETTINGS,
                           **({"speed": float(os.environ["ELEVENLABS_SPEED"])} if os.environ.get("ELEVENLABS_SPEED") else {})},
    }

    print(f"[generate_voice] Calling ElevenLabs ({model}, voice {voice_id[:8]}…)")
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs API error {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def decode_audio(audio_b64: str, out_dir: str) -> str:
    """Decode base64 audio and convert to the pipeline's voice.wav format via ffmpeg."""
    raw_path = os.path.join(out_dir, "_raw_tts.mp3")
    wav_path = os.path.join(out_dir, "voice.wav")

    raw_bytes = base64.b64decode(audio_b64)
    with open(raw_path, "wb") as f:
        f.write(raw_bytes)

    # Convert to 48 kHz mono 24-bit WAV (pipeline standard via dsp.py SR=48000)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", raw_path,
        "-ar", str(REQUIRED_SR),
        "-ac", str(REQUIRED_CHANNELS),
        "-acodec", "pcm_s24le",  # 24-bit ("-sample_fmt s24" is not a valid ffmpeg sample format)
        wav_path,
    ]
    print(f"[generate_voice] Converting to {REQUIRED_SR} Hz mono 24-bit WAV …")
    subprocess.run(cmd, check=True)

    # Clean up raw file
    os.remove(raw_path)
    print(f"[generate_voice] Wrote {wav_path}")
    return wav_path


# ---------------------------------------------------------------------------
# Character-to-word aggregation
# ---------------------------------------------------------------------------
def aggregate_words(
    characters: list[str],
    char_starts: list[float],
    char_ends: list[float],
) -> list[dict]:
    """Convert character-level timestamps into word-level timestamps.

    Returns a list of dicts matching the pipeline's words.json schema:
        [{"w": "word", "s": start_seconds, "e": end_seconds}, …]
    """
    words: list[dict] = []
    current_chars: list[str] = []
    current_start: float | None = None
    current_end: float = 0.0

    for ch, cs, ce in zip(characters, char_starts, char_ends):
        # Whitespace or explicit word boundary
        if ch.strip() == "":
            if current_chars:
                word_text = "".join(current_chars).strip()
                if word_text and current_start is not None:
                    words.append({
                        "w": word_text,
                        "s": round(current_start, 4),
                        "e": round(current_end, 4),
                    })
                current_chars = []
                current_start = None
            continue

        if current_start is None:
            current_start = cs
        current_chars.append(ch)
        current_end = ce

    # Flush the last word
    if current_chars:
        word_text = "".join(current_chars).strip()
        if word_text and current_start is not None:
            words.append({
                "w": word_text,
                "s": round(current_start, 4),
                "e": round(current_end, 4),
            })

    return words


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
def validate_words(words: list[dict]) -> list[str]:
    """Run automated sanity checks on the word timestamps. Returns warnings."""
    warnings: list[str] = []

    if not words:
        warnings.append("ERROR: words list is empty")
        return warnings

    prev_end = 0.0
    for i, w in enumerate(words):
        duration = w["e"] - w["s"]
        if duration <= 0:
            warnings.append(
                f"ERROR: word {i} '{w['w']}' has zero/negative duration "
                f"({w['s']:.4f} → {w['e']:.4f} = {duration:.4f}s)"
            )
        if w["s"] < prev_end - 0.001:
            warnings.append(
                f"WARNING: word {i} '{w['w']}' starts at {w['s']:.4f}s "
                f"before previous word ended at {prev_end:.4f}s (non-monotonic)"
            )
        prev_end = w["e"]

    total_duration = words[-1]["e"]
    lo, hi = TARGET_DURATION
    if total_duration < lo:
        warnings.append(
            f"WARNING: total duration {total_duration:.2f}s is below the "
            f"{lo}s target minimum (script may be too short)"
        )
    elif total_duration > hi:
        warnings.append(
            f"WARNING: total duration {total_duration:.2f}s exceeds the "
            f"{hi}s target maximum (script may be too long)"
        )
    else:
        print(
            f"[generate_voice] ✓ Duration {total_duration:.2f}s is within "
            f"the {lo}–{hi}s target window"
        )

    word_count = len(words)
    wps = word_count / total_duration if total_duration > 0 else 0
    print(f"[generate_voice] {word_count} words, {wps:.1f} words/s "
          f"(target ~3.7 w/s)")

    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    script_path = sys.argv[1]
    out_dir = os.path.expanduser(sys.argv[2])
    os.makedirs(out_dir, exist_ok=True)

    load_env()

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set (check .env or environment)")
        sys.exit(1)

    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # "Adam" default
    model = os.environ.get("ELEVENLABS_MODEL", DEFAULT_MODEL)

    # 1. Extract narration text
    narration = extract_narration(script_path)
    print(f"[generate_voice] Narration: {len(narration)} chars, "
          f"~{len(narration.split())} words")

    # 2. Call ElevenLabs
    result = call_elevenlabs(narration, api_key, voice_id, model)

    # 3. Decode and convert audio
    audio_b64 = result.get("audio_base64")
    if not audio_b64:
        raise RuntimeError("No audio_base64 in ElevenLabs response")
    decode_audio(audio_b64, out_dir)

    # 4. Aggregate character-level → word-level timestamps
    alignment = result.get("alignment", {})
    characters = alignment.get("characters", [])
    char_starts = alignment.get("character_start_times_seconds", [])
    char_ends = alignment.get("character_end_times_seconds", [])

    if not characters:
        raise RuntimeError("No character alignment data in ElevenLabs response")

    words = aggregate_words(characters, char_starts, char_ends)

    # 5. Validate
    warnings = validate_words(words)
    for w in warnings:
        print(f"[generate_voice] {w}")

    errors = [w for w in warnings if w.startswith("ERROR")]
    if errors:
        print(f"\n[generate_voice] ✗ {len(errors)} error(s) found — "
              "words.json written but needs manual review")

    # 6. Write words.json
    words_path = os.path.join(out_dir, "words.json")
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)
    print(f"[generate_voice] Wrote {words_path} ({len(words)} words)")

    # Summary
    if words:
        total = words[-1]["e"]
        print(f"\n[generate_voice] ✓ Done: voice.wav ({REQUIRED_SR} Hz mono) "
              f"+ words.json ({len(words)} words, {total:.2f}s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())

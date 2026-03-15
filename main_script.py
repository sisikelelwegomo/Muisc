import os
import subprocess
import random
import shutil
import re
from multiprocessing import Pool

# --- FIXED CONFIG ---
# Get the directory where the script is actually running
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Use relative names (no leading slashes)
VIDEO_DIR = os.path.join(BASE_DIR, "IGClips")
AUDIO_DIR = os.path.join(BASE_DIR, "music_source")
OUTPUT_DIR = os.path.join(BASE_DIR, "batch_output")

VIDEO_DURATION = 32.5
THREADS = 4
LYRICS_MODE = "whisper"


def resolve_ffmpeg_exe():
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        return env_path

    local_candidates = [
        os.path.join(BASE_DIR, "ffmpeg.exe"),
        os.path.join(BASE_DIR, "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("ffmpeg")


def resolve_ffprobe_exe():
    env_path = os.environ.get("FFPROBE_PATH")
    if env_path:
        return env_path

    local_candidates = [
        os.path.join(BASE_DIR, "ffprobe.exe"),
        os.path.join(BASE_DIR, "ffmpeg", "bin", "ffprobe.exe"),
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe

    ffmpeg = resolve_ffmpeg_exe()
    if ffmpeg and ffmpeg.lower().endswith("ffmpeg.exe"):
        alongside = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.exists(alongside):
            return alongside

    return None


def ensure_ffmpeg_on_path():
    ffmpeg_exe = resolve_ffmpeg_exe()
    if not ffmpeg_exe:
        return False
    ffmpeg_dir = os.path.dirname(ffmpeg_exe)
    current = os.environ.get("PATH") or ""
    parts = current.split(os.pathsep) if current else []
    if ffmpeg_dir not in parts:
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + current
    return True


def ffmpeg_filter_escape_path(path):
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    p = p.replace(" ", "\\ ")
    return p


def format_srt_time(seconds):
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000.0))
    hours, rem = divmod(total_ms, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def get_audio_duration_seconds(audio_path):
    try:
        if audio_path.lower().endswith(".wav"):
            import wave

            with wave.open(audio_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate <= 0:
                    return None
                return frames / float(rate)
    except Exception:
        pass

    ffprobe = resolve_ffprobe_exe()
    if not ffprobe:
        return None

    probe_cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    completed = subprocess.run(probe_cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    raw = (completed.stdout or "").strip()
    try:
        duration = float(raw)
    except Exception:
        return None
    if duration <= 0:
        return None
    return duration


def find_sidecar_lyrics_file(audio_path):
    base = os.path.splitext(audio_path)[0]
    candidates = [
        base + ".srt",
        base + ".ass",
        base + ".lrc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def parse_lrc_lines(lrc_text):
    pattern = re.compile(r"^\s*\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)\s*$")
    entries = []
    for raw_line in lrc_text.splitlines():
        m = pattern.match(raw_line)
        if not m:
            continue
        mm = int(m.group(1))
        ss = float(m.group(2))
        text = m.group(3).strip()
        start = mm * 60.0 + ss
        if text:
            entries.append((start, text))
    entries.sort(key=lambda x: x[0])
    return entries


def write_srt(segments, out_srt_path, fallback_end=None):
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = float(seg["start"])
        end = float(seg["end"])
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        lines.append(text)
        lines.append("")
    if not lines and fallback_end is not None:
        lines = [
            "1",
            f"{format_srt_time(0)} --> {format_srt_time(float(fallback_end))}",
            "(no lyrics detected)",
            "",
        ]
    os.makedirs(os.path.dirname(out_srt_path), exist_ok=True)
    with open(out_srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


_WHISPER_MODEL = None


def get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    import whisper

    model_name = (os.environ.get("WHISPER_MODEL") or "base").strip()
    _WHISPER_MODEL = whisper.load_model(model_name)
    return _WHISPER_MODEL


def try_generate_lyrics_srt_from_whisper(audio_path, out_srt_path):
    try:
        model = get_whisper_model()
    except Exception:
        return None

    ensure_ffmpeg_on_path()

    try:
        result = model.transcribe(audio_path, fp16=False)
    except Exception:
        return None

    raw_segments = result.get("segments") or []
    segments = []
    for seg in raw_segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            {"start": float(seg.get("start", 0.0)), "end": float(seg.get("end", 0.0)), "text": text}
        )

    duration = get_audio_duration_seconds(audio_path)
    if duration is None:
        duration = VIDEO_DURATION
    write_srt(segments, out_srt_path, fallback_end=duration)
    return out_srt_path


def try_convert_lrc_to_srt(lrc_path, out_srt_path, audio_path=None):
    try:
        with open(lrc_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return None

    entries = parse_lrc_lines(text)
    if not entries:
        return None

    duration = None
    if audio_path:
        duration = get_audio_duration_seconds(audio_path)
    if duration is None:
        duration = VIDEO_DURATION

    segments = []
    for i, (start, lyric) in enumerate(entries):
        if i + 1 < len(entries):
            end = max(start + 0.1, entries[i + 1][0] - 0.05)
        else:
            end = max(start + 1.0, float(duration))
        segments.append({"start": start, "end": end, "text": lyric})

    write_srt(segments, out_srt_path, fallback_end=duration)
    return out_srt_path


def generate_video(index):
    try:
        # Create output dir if it doesn't exist
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR, exist_ok=True)

        # 1. Check if folders exist and have files
        if not os.path.exists(VIDEO_DIR):
            return f"❌ Folder not found: {VIDEO_DIR}"

        if not os.path.exists(AUDIO_DIR):
            return f"❌ Folder not found: {AUDIO_DIR}"

        all_videos = [f for f in os.listdir(
            VIDEO_DIR) if f.endswith(('.mp4', '.mov'))]
        if not all_videos:
            return f"❌ No videos found in: {VIDEO_DIR}"

        all_audio = [f for f in os.listdir(
            AUDIO_DIR) if f.endswith(('.mp3', '.wav'))]
        if not all_audio:
            return f"❌ No audio found in: {AUDIO_DIR}"

        ffmpeg_exe = resolve_ffmpeg_exe()
        if not ffmpeg_exe:
            return (
                "❌ ffmpeg not found. Install ffmpeg and ensure it's in PATH, "
                "or set FFMPEG_PATH to the full ffmpeg.exe path."
            )

        ensure_ffmpeg_on_path()

        selected_video = os.path.join(VIDEO_DIR, random.choice(all_videos))
        selected_audio = os.path.join(AUDIO_DIR, random.choice(all_audio))

        audio_name = os.path.splitext(os.path.basename(selected_audio))[0]
        output_name = os.path.join(
            OUTPUT_DIR, f"{audio_name}_clip_{index+1:02d}.mp4")

        h_flip = random.choice([True, False])
        subtitle_path = None
        if LYRICS_MODE in {"sidecar", "auto", "whisper"}:
            desired_srt = os.path.splitext(output_name)[0] + ".srt"
            sidecar = find_sidecar_lyrics_file(selected_audio)
            if sidecar and LYRICS_MODE in {"sidecar", "auto"}:
                if sidecar.lower().endswith(".srt") or sidecar.lower().endswith(".ass"):
                    subtitle_path = sidecar
                elif sidecar.lower().endswith(".lrc"):
                    subtitle_path = try_convert_lrc_to_srt(sidecar, desired_srt, audio_path=selected_audio)
            elif LYRICS_MODE in {"whisper", "auto"}:
                subtitle_path = try_generate_lyrics_srt_from_whisper(selected_audio, desired_srt)
                if LYRICS_MODE == "whisper" and not subtitle_path:
                    return (
                        f"❌ Whisper failed to generate lyrics for: {os.path.basename(selected_audio)}"
                    )

        # 2. FFmpeg Command
        filters = []
        if h_flip:
            filters.append("hflip")
        if subtitle_path:
            escaped = ffmpeg_filter_escape_path(subtitle_path)
            style = "Alignment=2,FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,BorderStyle=1,Outline=2"
            filters.append(f"subtitles='{escaped}':force_style='{style}'")

        cmd = [
            ffmpeg_exe, '-y',
            '-stream_loop', '-1', '-i', selected_video,
            '-i', selected_audio,
            '-map', '0:v',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '23',
            '-t', str(VIDEO_DURATION),
            '-pix_fmt', 'yuv420p',
        ]

        if filters:
            cmd.extend(["-vf", ",".join(filters)])

        cmd.append(output_name)

        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stderr_tail = "\n".join(stderr.splitlines()[-12:]) if stderr else "(no stderr)"
            return (
                f"❌ ffmpeg failed on video {index+1} (exit {completed.returncode}).\n"
                f"{stderr_tail}"
            )

        return f"✅ Created: {os.path.basename(output_name)}"

    except Exception as e:
        return f"❌ Error on video {index+1}: {e}"


if __name__ == "__main__":
    if not resolve_ffmpeg_exe():
        print(
            "❌ ffmpeg not found. Install ffmpeg and ensure it's in PATH, "
            "or set FFMPEG_PATH to the full ffmpeg.exe path."
        )
        raise SystemExit(2)

    if LYRICS_MODE in {"whisper", "auto"}:
        try:
            import whisper  # noqa: F401
        except Exception:
            print(
                "⚠️ LYRICS_MODE is set to whisper/auto, but the 'whisper' package isn't installed.\n"
                "   Install it with: pip install -U openai-whisper"
            )

    print(f"🎬 Processing 10 videos from MP3/WAV sources...")
    # This will help you verify the path in the terminal
    print(f"📂 Looking in: {VIDEO_DIR}")

    threads = 1 if LYRICS_MODE in {"whisper", "auto"} else THREADS
    with Pool(threads) as p:
        results = p.map(generate_video, range(10))
        for r in results:
            print(r)

    print(f"\n✨ Batch complete.")

import os
import subprocess
import random
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "IGClips2")
AUDIO_DIR = os.path.join(BASE_DIR, "music_source2")
OUTPUT_DIR = os.path.join(BASE_DIR, "RealMadrid")


THREADS = 4
VIDEO_AUDIO_VOLUME = 0.0
SONG_AUDIO_VOLUME = 1.0
CAPTION_WRAP_CHARS = 18

AUDIO_DIRS = [AUDIO_DIR]
_extra_audio_dirs = (os.environ.get("AUDIO_DIRS") or "").strip()
if _extra_audio_dirs:
    for raw in _extra_audio_dirs.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        AUDIO_DIRS.append(raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw))

AUDIO_START_TIME = float(os.environ.get("AUDIO_START_TIME") or "13.5")
AUDIO_END_TIME = float(os.environ.get("AUDIO_END_TIME") or "27.9")
DEFAULT_VIDEO_DURATION = float(os.environ.get("VIDEO_DURATION") or "32.8")
VIDEO_DURATION = (AUDIO_END_TIME - AUDIO_START_TIME) if AUDIO_END_TIME > AUDIO_START_TIME else DEFAULT_VIDEO_DURATION
AUDIO_TIMINGS_FILE = os.environ.get("AUDIO_TIMINGS_FILE") or os.path.join(BASE_DIR, "audio_times.csv")
_AUDIO_TIMINGS_CACHE = None
_AUDIO_TIMINGS_MTIME = None


def resolve_ffmpeg_exe():
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        return env_path
    local_candidates = [
        os.path.join(BASE_DIR, "ffmpeg.exe"),
        os.path.join(BASE_DIR, "ffmpeg", "bin", "ffmpeg.exe")
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("ffmpeg")


def pick_hook_caption():
    hooks_path = os.path.join(BASE_DIR, "money.txt")
    try:
        with open(hooks_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip()
                     for line in f.read().splitlines() if line.strip()]
    except Exception:
        lines = []
    return random.choice(lines).encode('ascii', 'ignore').decode('ascii').strip() if lines else "The Grind Never Stops"


def wrap_caption(text, max_chars):
    words = text.split()
    lines, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = (current + " " + word).strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def ffmpeg_filter_escape_path(path):
    return os.path.abspath(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(" ", "\\ ")


def resolve_bold_fontfile():
    windir = os.environ.get("WINDIR") or "C:\\Windows"
    candidates = [os.path.join(windir, "Fonts", "arialbd.ttf"), os.path.join(
        windir, "Fonts", "ARIALBD.TTF")]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def parse_time_seconds(value):
    s = str(value).strip()
    if not s:
        raise ValueError("empty time")
    if ":" in s:
        parts = [p.strip() for p in s.split(":")]
        if len(parts) == 2:
            mm, ss = parts
            return float(mm) * 60.0 + float(ss)
        if len(parts) == 3:
            hh, mm, ss = parts
            return float(hh) * 3600.0 + float(mm) * 60.0 + float(ss)
        raise ValueError(f"invalid time: {value}")
    return float(s)


def parse_time_range(value):
    s = str(value).strip()
    if not s:
        raise ValueError("empty time range")
    if "-" in s:
        a, b = [p.strip() for p in s.split("-", 1)]
        return parse_time_seconds(a), parse_time_seconds(b)
    return parse_time_seconds(s), None


def load_audio_timings(path):
    timings = {}
    if not path or not os.path.exists(path):
        return timings
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f.read().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                name = parts[0].lower()
                try:
                    if len(parts) == 2:
                        start, end = parse_time_range(parts[1])
                    else:
                        start = parse_time_seconds(parts[1])
                        end = parse_time_seconds(parts[2]) if parts[2] != "" else None
                except Exception:
                    continue
                if end is not None and end <= 0:
                    end = None
                if name not in timings:
                    timings[name] = []
                timings[name].append((start, end))
    except Exception:
        return {}
    return timings


def get_audio_timings():
    global _AUDIO_TIMINGS_CACHE
    global _AUDIO_TIMINGS_MTIME
    path = AUDIO_TIMINGS_FILE
    try:
        mtime = os.path.getmtime(path) if path and os.path.exists(path) else None
    except Exception:
        mtime = None

    if _AUDIO_TIMINGS_CACHE is None or _AUDIO_TIMINGS_MTIME != mtime:
        _AUDIO_TIMINGS_CACHE = load_audio_timings(path)
        _AUDIO_TIMINGS_MTIME = mtime

    return _AUDIO_TIMINGS_CACHE or {}


def resolve_audio_timing(audio_path, timings_map, pick_index):
    key = os.path.basename(audio_path).lower()
    if key in timings_map:
        ranges = timings_map[key]
        return ranges[int(pick_index) % len(ranges)]
    key_no_ext = os.path.splitext(key)[0]
    if key_no_ext in timings_map:
        ranges = timings_map[key_no_ext]
        return ranges[int(pick_index) % len(ranges)]
    for k, ranges in timings_map.items():
        if k.endswith("/" + key) or k.endswith("\\" + key):
            return ranges[int(pick_index) % len(ranges)]
        if key_no_ext and (k.endswith("/" + key_no_ext) or k.endswith("\\" + key_no_ext)):
            return ranges[int(pick_index) % len(ranges)]
    end = AUDIO_END_TIME if AUDIO_END_TIME > AUDIO_START_TIME else None
    return AUDIO_START_TIME, end


def generate_video(index):
    try:
        timings_map = get_audio_timings()
        raw_caption = pick_hook_caption()
        caption_lines = wrap_caption(raw_caption, CAPTION_WRAP_CHARS)
        if not caption_lines:
            caption_lines = ["The Grind Never Stops"]

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR, exist_ok=True)

        if not os.path.isdir(VIDEO_DIR):
            return f"❌ Folder not found: {VIDEO_DIR}"

        all_videos = sorted([f for f in os.listdir(VIDEO_DIR) if f.endswith((".mp4", ".mov"))])
        if not all_videos:
            return f"❌ No videos found in: {VIDEO_DIR}"

        all_audio = []
        for d in AUDIO_DIRS:
            if not os.path.isdir(d):
                continue
            all_audio.extend([os.path.join(d, f) for f in os.listdir(d) if f.endswith((".mp3", ".wav"))])

        selected_video = os.path.join(VIDEO_DIR, all_videos[index % len(all_videos)])
        if not all_audio:
            return f"❌ No audio found in: {', '.join(AUDIO_DIRS)}"
        all_audio = sorted(all_audio, key=lambda p: os.path.basename(p).lower())
        selected_audio = all_audio[index % len(all_audio)]
        start_t, end_t = resolve_audio_timing(selected_audio, timings_map, index)
        clip_duration = (end_t - start_t) if (end_t is not None and end_t > start_t) else VIDEO_DURATION
        output_name = os.path.join(OUTPUT_DIR, f"money_clip_{index+1:02d}.mp4")

        font_esc = ffmpeg_filter_escape_path(resolve_bold_fontfile())

        # UI/Text positioning logic
        font_size, line_spacing = 60, 15
        line_h = font_size + line_spacing
        y0 = f"(h/2)-({line_h}*({len(caption_lines)}-1))/2"

        draw_filters, temp_files = [], []
        for i, line in enumerate(caption_lines):
            cap_path = os.path.join(
                OUTPUT_DIR, f"_cap_{os.getpid()}_{index+1:02d}_{i+1:02d}.txt")
            with open(cap_path, "w", encoding="utf-8", newline="") as f:
                f.write(line)
            temp_files.append(cap_path)
            cap_esc = ffmpeg_filter_escape_path(cap_path)
            draw_filters.append(
                f"drawtext=fontfile='{font_esc}':textfile='{cap_esc}':reload=0:"
                f"x=(w-text_w)/2:y=({y0})+({i}*{line_h})-(text_h/2):"
                f"fontcolor=white:bordercolor=black:borderw=4:fontsize={font_size}:fix_bounds=1"
            )

        drawtext = ",".join(draw_filters)

        audio_filter = f"atrim=0:{clip_duration},asetpts=PTS-STARTPTS,apad=pad_dur={clip_duration},atrim=0:{clip_duration}"

        cmd = [
            resolve_ffmpeg_exe(), "-y", "-loglevel", "error",
            "-stream_loop", "-1", "-i", selected_video,
            "-ss", str(start_t),
            "-t", str(clip_duration),
            "-i", selected_audio,
            "-t", str(clip_duration),
            "-vf", drawtext,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-af", audio_filter,
            output_name
        ]

        try:
            subprocess.run(cmd, check=True)
        finally:
            for p in temp_files:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

        audio_label = os.path.basename(selected_audio)
        timing_label = f"{start_t}->{end_t}" if end_t is not None else f"{start_t}->{start_t + clip_duration}"
        return f"✅ Created: {os.path.basename(output_name)} | {audio_label} @ {timing_label}"
    except Exception as e:
        return f"❌ Error: {e}"


if __name__ == "__main__":
    count = 4
    print(
        f"🎬 Creating {count} clips ({AUDIO_START_TIME}s to {AUDIO_END_TIME}s)...")
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(generate_video, i) for i in range(count)]
        for f in as_completed(futures):
            print(f.result())
    print("✨ Complete.")

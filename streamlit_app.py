import os
import random
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_ffmpeg_exe(base_dir):
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        return env_path

    local_candidates = [
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("ffmpeg")


def resolve_ffprobe_exe(base_dir):
    env_path = os.environ.get("FFPROBE_PATH")
    if env_path:
        return env_path

    local_candidates = [
        os.path.join(base_dir, "ffprobe.exe"),
        os.path.join(base_dir, "ffmpeg", "bin", "ffprobe.exe"),
    ]
    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe

    ffmpeg = resolve_ffmpeg_exe(base_dir)
    if ffmpeg and ffmpeg.lower().endswith("ffmpeg.exe"):
        alongside = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.exists(alongside):
            return alongside

    return None


def ffmpeg_filter_escape_path(path):
    return (
        os.path.abspath(path)
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )


def resolve_bold_fontfile():
    candidates = [
        os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts", "arialbd.ttf"),
        os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts", "ARIALBD.TTF"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def strip_emojis(text):
    s = str(text)
    out = []
    for ch in s:
        cp = ord(ch)
        if 0x1F000 <= cp <= 0x1FAFF:
            continue
        if 0x2600 <= cp <= 0x27BF:
            continue
        if 0xFE00 <= cp <= 0xFE0F:
            continue
        out.append(ch)
    return "".join(out)


def wrap_caption(text, max_chars):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def list_media_files(dir_path, exts):
    if not os.path.isdir(dir_path):
        return []
    files = []
    for name in os.listdir(dir_path):
        p = os.path.join(dir_path, name)
        if os.path.isfile(p) and name.lower().endswith(exts):
            files.append(p)
    return sorted(files)


def parse_audio_timings(text):
    timings = {}
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        start_s = parts[1] if len(parts) >= 2 else ""
        end_s = parts[2] if len(parts) >= 3 else ""

        def _parse_time_seconds(v):
            s = str(v).strip()
            if not s:
                raise ValueError("empty time")
            if ":" in s:
                segs = [p.strip() for p in s.split(":")]
                if len(segs) == 2:
                    mm, ss = segs
                    return float(mm) * 60.0 + float(ss)
                if len(segs) == 3:
                    hh, mm, ss = segs
                    return float(hh) * 3600.0 + float(mm) * 60.0 + float(ss)
                raise ValueError("bad time")
            return float(s)

        def _parse_time_range(v):
            s = str(v).strip()
            if "-" in s:
                a, b = [p.strip() for p in s.split("-", 1)]
                return _parse_time_seconds(a), _parse_time_seconds(b)
            return _parse_time_seconds(s), None

        try:
            if len(parts) == 2:
                start, end = _parse_time_range(start_s)
            else:
                start = _parse_time_seconds(start_s)
                end = _parse_time_seconds(end_s) if end_s != "" else None
        except Exception:
            continue

        if end is not None and end <= 0:
            end = None

        key = name.strip().lower()
        if key not in timings:
            timings[key] = []
        timings[key].append((start, end))
    return timings


def resolve_audio_timing(audio_path, timings_map, default_start, default_end, pick_index):
    key = os.path.basename(audio_path).lower()
    if key in timings_map:
        ranges = timings_map[key]
        return ranges[int(pick_index) % len(ranges)]

    key_no_ext = os.path.splitext(key)[0]
    if key_no_ext in timings_map:
        ranges = timings_map[key_no_ext]
        return ranges[int(pick_index) % len(ranges)]

    for k, v in timings_map.items():
        if k.endswith("/" + key) or k.endswith("\\" + key):
            return v[int(pick_index) % len(v)]
        if key_no_ext and (k.endswith("/" + key_no_ext) or k.endswith("\\" + key_no_ext)):
            return v[int(pick_index) % len(v)]

    start = float(default_start) if default_start is not None else None
    end = float(default_end) if default_end is not None else None
    return start, end


def video_has_audio(ffprobe_exe, video_path):
    if not ffprobe_exe:
        return False
    cmd = [
        ffprobe_exe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        return False
    return (completed.stdout or "").strip() == "audio"


def build_drawtext_filter(lines, fontfile, font_size, borderw, line_spacing):
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        lines = ["POV: The Grind Never Stops"]

    if fontfile:
        font_esc = ffmpeg_filter_escape_path(fontfile)
        font_opt = f"fontfile='{font_esc}':"
    else:
        font_opt = ""

    line_h = int(font_size) + int(line_spacing)
    y0 = f"(h/2)-({line_h}*({len(lines)}-1))/2"

    temp_files = []
    draw_filters = []
    for i, line in enumerate(lines):
        fd, p = tempfile.mkstemp(prefix="cap_", suffix=".txt")
        os.close(fd)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(line)
        temp_files.append(p)
        cap_esc = ffmpeg_filter_escape_path(p)
        draw_filters.append(
            "drawtext="
            f"{font_opt}"
            f"textfile='{cap_esc}':reload=0:"
            "x=(w-text_w)/2:"
            f"y=({y0})+({i}*{line_h})-(text_h/2):"
            f"fontsize={int(font_size)}:fontcolor=white:bordercolor=black:borderw={int(borderw)}:"
            "fix_bounds=1:text_shaping=1:expansion=none"
        )

    return ",".join(draw_filters), temp_files


def generate_one(
    *,
    base_dir,
    video_dir,
    audio_dirs,
    output_dir,
    hooks_lines,
    clip_index,
    duration_seconds,
    song_volume,
    video_volume,
    audio_start_seconds,
    audio_end_seconds,
    use_snippet_duration,
    audio_timings_map,
    wrap_chars,
    font_size,
    borderw,
    line_spacing,
    include_video_audio,
):
    ffmpeg = resolve_ffmpeg_exe(base_dir)
    if not ffmpeg:
        return False, "ffmpeg not found"

    ffprobe = resolve_ffprobe_exe(base_dir)

    videos = list_media_files(video_dir, (".mp4", ".mov"))
    if not videos:
        return False, f"No videos found in {video_dir}"

    audios = []
    for d in audio_dirs:
        audios.extend(list_media_files(d, (".mp3", ".wav")))
    if not audios:
        return False, "No audio found in the selected audio folders"

    caption = random.choice(hooks_lines) if hooks_lines else "POV: The Grind Never Stops"
    caption = strip_emojis(caption)
    lines = wrap_caption(caption, wrap_chars)

    fontfile = resolve_bold_fontfile()
    vf, temp_files = build_drawtext_filter(lines, fontfile, font_size, borderw, line_spacing)

    os.makedirs(output_dir, exist_ok=True)

    videos = sorted(videos, key=lambda p: os.path.basename(p).lower())
    audios = sorted(audios, key=lambda p: os.path.basename(p).lower())
    idx = int(clip_index)
    selected_video = videos[idx % len(videos)]
    selected_audio = audios[idx % len(audios)]
    out_name = os.path.join(output_dir, f"clip_{idx+1:03d}.mp4")

    try:
        has_vid_audio = video_has_audio(ffprobe, selected_video) if include_video_audio else False

        clip_duration = float(duration_seconds)
        start, end = resolve_audio_timing(
            selected_audio,
            audio_timings_map or {},
            audio_start_seconds,
            audio_end_seconds,
            idx,
        )
        input_audio_duration = clip_duration
        if start is not None and end is not None:
            if end <= start:
                return False, f"Audio end time must be greater than start time for {os.path.basename(selected_audio)}"
            available = float(end - start)
            if use_snippet_duration:
                clip_duration = available
                input_audio_duration = clip_duration
            else:
                input_audio_duration = min(clip_duration, available)

        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-stream_loop",
            "-1",
            "-i",
            selected_video,
        ]

        if start is not None and end is not None:
            cmd.extend(["-ss", str(start), "-t", str(input_audio_duration), "-i", selected_audio])
        else:
            cmd.extend(["-stream_loop", "-1", "-i", selected_audio])

        cmd.extend(
            [
                "-t",
                str(clip_duration),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        )

        if has_vid_audio:
            sa = (
                f"[1:a]atrim=0:{input_audio_duration},asetpts=PTS-STARTPTS,"
                f"apad=pad_dur={clip_duration},atrim=0:{clip_duration},"
                f"volume={float(song_volume)}[sa]"
            )
            va = f"[0:a]volume={float(video_volume)}[va]"
            cmd.extend(
                [
                    "-filter_complex",
                    f"{va};{sa};[va][sa]amix=inputs=2:duration=longest:dropout_transition=2,atrim=0:{clip_duration}[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                ]
            )
        else:
            af = (
                f"atrim=0:{input_audio_duration},asetpts=PTS-STARTPTS,"
                f"apad=pad_dur={clip_duration},atrim=0:{clip_duration},"
                f"volume={float(song_volume)}"
            )
            cmd.extend(["-map", "0:v", "-map", "1:a", "-af", af])

        cmd.append(out_name)

        completed = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            return False, stderr or "ffmpeg failed"

        return True, out_name
    finally:
        for p in temp_files:
            try:
                os.remove(p)
            except Exception:
                pass


if os.environ.get("MUISC_SELF_TEST") == "1":
    candidates_v = [
        os.path.join(BASE_DIR, "IGClips2"),
        os.path.join(BASE_DIR, "IGClips"),
        os.path.join(BASE_DIR, "aesthetic_loops"),
    ]
    candidates_a = [
        os.path.join(BASE_DIR, "music_source2"),
        os.path.join(BASE_DIR, "music_source"),
    ]
    vdir = next((p for p in candidates_v if os.path.isdir(p)), None)
    adirs = [p for p in candidates_a if os.path.isdir(p)]
    if not vdir or not adirs:
        print("Self test skipped: missing media folders")
        raise SystemExit(0)
    if not list_media_files(vdir, (".mp4", ".mov")):
        print("Self test skipped: no videos found")
        raise SystemExit(0)
    audio_ok = False
    for d in adirs:
        if list_media_files(d, (".mp3", ".wav")):
            audio_ok = True
            break
    if not audio_ok:
        print("Self test skipped: no audio found")
        raise SystemExit(0)
    ok, msg = generate_one(
        base_dir=BASE_DIR,
        video_dir=vdir,
        audio_dirs=adirs,
        output_dir=os.path.join(BASE_DIR, "Fibbo"),
        hooks_lines=["POV: test"],
        clip_index=0,
        duration_seconds=5.0,
        song_volume=1.0,
        video_volume=0.0,
        audio_start_seconds=0.0,
        audio_end_seconds=2.5,
        use_snippet_duration=True,
        audio_timings_map={},
        wrap_chars=22,
        font_size=60,
        borderw=4,
        line_spacing=15,
        include_video_audio=False,
    )
    print(msg)
    raise SystemExit(0 if ok else 1)


st.set_page_config(page_title="Muisc Clip Generator", layout="centered")
st.title("Muisc Clip Generator")

with st.sidebar:
    st.subheader("Folders")
    video_dir = st.text_input("Video folder", value=os.path.join(BASE_DIR, "IGClips"))
    audio_dirs_text = st.text_area(
        "Audio folders (one per line)",
        value=os.path.join(BASE_DIR, "music_source"),
        height=90,
    )
    output_dir = st.text_input("Output folder", value=os.path.join(BASE_DIR, "Fibbo"))

    st.subheader("Video")
    duration_seconds = st.number_input("Duration (seconds)", min_value=1.0, max_value=600.0, value=32.8, step=0.5)
    threads = st.slider("Parallel jobs", min_value=1, max_value=8, value=4)

    st.subheader("Audio")
    include_video_audio = st.toggle("Mix original video audio", value=False)
    song_volume = st.slider("Song volume", min_value=0.0, max_value=2.0, value=1.0, step=0.05)
    video_volume = st.slider("Video volume", min_value=0.0, max_value=2.0, value=0.0, step=0.05)
    audio_start_seconds = st.number_input("Audio start (seconds)", min_value=0.0, max_value=36000.0, value=0.0, step=0.5)
    audio_end_seconds = st.number_input("Audio end (seconds, 0 = disable)", min_value=0.0, max_value=36000.0, value=0.0, step=0.5)
    use_snippet_duration = st.toggle("Use snippet duration as clip duration", value=True)

    st.subheader("Captions")
    wrap_chars = st.slider("Wrap width (chars)", min_value=10, max_value=40, value=22)
    font_size = st.slider("Font size", min_value=20, max_value=90, value=60)
    borderw = st.slider("Border width", min_value=1, max_value=10, value=4)
    line_spacing = st.slider("Line spacing", min_value=0, max_value=40, value=15)

st.subheader("Hooks")
default_hooks_path = os.path.join(BASE_DIR, "hooks.txt")
default_hooks_text = ""
if os.path.exists(default_hooks_path):
    try:
        with open(default_hooks_path, "r", encoding="utf-8", errors="ignore") as f:
            default_hooks_text = f.read()
    except Exception:
        default_hooks_text = ""

hooks_text = st.text_area("One hook per line", value=default_hooks_text, height=200, placeholder="POV: The grind never stops")
hooks_lines = [line.strip() for line in hooks_text.splitlines() if line.strip()]

st.subheader("Per-song Audio Times")
timings_text = st.text_area(
    "CSV lines: filename,start,end  (end optional; 0 = disable)",
    value="",
    height=140,
    placeholder="Fibbonacci.wav,13.5,27.9\nOtherSong.mp3,0,15.2",
)
audio_timings_map = parse_audio_timings(timings_text)

audio_dirs = [line.strip() for line in audio_dirs_text.splitlines() if line.strip()]

col1, col2 = st.columns(2)
with col1:
    count = st.number_input("How many clips?", min_value=1, max_value=50, value=10, step=1)
with col2:
    seed = st.number_input("Random seed (optional)", min_value=0, max_value=999999, value=0, step=1)

ffmpeg_path = resolve_ffmpeg_exe(BASE_DIR)
if not ffmpeg_path:
    st.error("ffmpeg not found. Put ffmpeg in PATH or add it under .\\ffmpeg\\bin\\ffmpeg.exe or set FFMPEG_PATH.")
else:
    st.caption(f"ffmpeg: {ffmpeg_path}")

run = st.button("Generate")

if run:
    if seed:
        random.seed(int(seed))

    errors = []
    outputs = []
    progress = st.progress(0)
    status = st.empty()

    def _job(_):
        return generate_one(
            base_dir=BASE_DIR,
            video_dir=video_dir,
            audio_dirs=audio_dirs,
            output_dir=output_dir,
            hooks_lines=hooks_lines,
            clip_index=_,
            duration_seconds=duration_seconds,
            song_volume=song_volume,
            video_volume=video_volume,
            audio_start_seconds=(audio_start_seconds if audio_end_seconds and audio_end_seconds > 0 else None),
            audio_end_seconds=(audio_end_seconds if audio_end_seconds and audio_end_seconds > 0 else None),
            use_snippet_duration=use_snippet_duration,
            audio_timings_map=audio_timings_map,
            wrap_chars=wrap_chars,
            font_size=font_size,
            borderw=borderw,
            line_spacing=line_spacing,
            include_video_audio=include_video_audio,
        )

    with ThreadPoolExecutor(max_workers=int(threads)) as executor:
        futures = [executor.submit(_job, i) for i in range(int(count))]
        done = 0
        for f in as_completed(futures):
            ok, msg = f.result()
            done += 1
            progress.progress(done / int(count))
            status.write(f"{done}/{int(count)}")
            if ok:
                outputs.append(msg)
            else:
                errors.append(msg)

    if outputs:
        st.success(f"Created {len(outputs)} clip(s)")
        for p in outputs[:10]:
            st.write(p)

    if errors:
        st.error(f"{len(errors)} error(s)")
        st.code("\n\n".join(errors[:5]))

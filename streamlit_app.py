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
    audio_dir,
    output_dir,
    hooks_lines,
    duration_seconds,
    song_volume,
    video_volume,
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

    audios = list_media_files(audio_dir, (".mp3", ".wav"))
    if not audios:
        return False, f"No audio found in {audio_dir}"

    caption = random.choice(hooks_lines) if hooks_lines else "POV: The Grind Never Stops"
    caption = strip_emojis(caption)
    lines = wrap_caption(caption, wrap_chars)

    fontfile = resolve_bold_fontfile()
    vf, temp_files = build_drawtext_filter(lines, fontfile, font_size, borderw, line_spacing)

    os.makedirs(output_dir, exist_ok=True)

    selected_video = random.choice(videos)
    selected_audio = random.choice(audios)
    out_name = os.path.join(output_dir, f"clip_{random.randint(100000, 999999)}.mp4")

    try:
        has_vid_audio = video_has_audio(ffprobe, selected_video) if include_video_audio else False

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
            "-stream_loop",
            "-1",
            "-i",
            selected_audio,
            "-t",
            str(duration_seconds),
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

        if has_vid_audio:
            cmd.extend(
                [
                    "-filter_complex",
                    f"[0:a]volume={float(video_volume)}[va];[1:a]volume={float(song_volume)}[sa];[va][sa]amix=inputs=2:duration=longest:dropout_transition=2[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                ]
            )
        else:
            cmd.extend(["-map", "0:v", "-map", "1:a", "-af", f"volume={float(song_volume)}"])

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


st.set_page_config(page_title="Muisc Clip Generator", layout="centered")
st.title("Muisc Clip Generator")

with st.sidebar:
    st.subheader("Folders")
    video_dir = st.text_input("Video folder", value=os.path.join(BASE_DIR, "IGClips"))
    audio_dir = st.text_input("Audio folder", value=os.path.join(BASE_DIR, "music_source"))
    output_dir = st.text_input("Output folder", value=os.path.join(BASE_DIR, "Fibbo"))

    st.subheader("Video")
    duration_seconds = st.number_input("Duration (seconds)", min_value=1.0, max_value=600.0, value=32.8, step=0.5)
    threads = st.slider("Parallel jobs", min_value=1, max_value=8, value=4)

    st.subheader("Audio")
    include_video_audio = st.toggle("Mix original video audio", value=False)
    song_volume = st.slider("Song volume", min_value=0.0, max_value=2.0, value=1.0, step=0.05)
    video_volume = st.slider("Video volume", min_value=0.0, max_value=2.0, value=0.0, step=0.05)

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
            audio_dir=audio_dir,
            output_dir=output_dir,
            hooks_lines=hooks_lines,
            duration_seconds=duration_seconds,
            song_volume=song_volume,
            video_volume=video_volume,
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

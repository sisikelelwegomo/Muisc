import os
import subprocess
import random
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "IGClips")
AUDIO_DIR = os.path.join(BASE_DIR, "music_source")
OUTPUT_DIR = os.path.join(BASE_DIR, "Fibbo")

VIDEO_DURATION = 32.8
THREADS = 4
VIDEO_AUDIO_VOLUME = 0.0
SONG_AUDIO_VOLUME = 1.0
FFMPEG_TIMEOUT_SECONDS = 0 
CAPTION_WRAP_CHARS = 18

def resolve_ffmpeg_exe():
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path: return env_path
    local_candidates = [os.path.join(BASE_DIR, "ffmpeg.exe"), os.path.join(BASE_DIR, "ffmpeg", "bin", "ffmpeg.exe")]
    for candidate in local_candidates:
        if os.path.exists(candidate): return candidate
    return shutil.which("ffmpeg")

def pick_hook_caption():
    hooks_path = os.path.join(BASE_DIR, "hooks.txt")
    try:
        with open(hooks_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f.read().splitlines() if line.strip()]
    except Exception:
        lines = []
    if not lines: return "The Grind Never Stops"
    
    raw_text = random.choice(lines)
    # This removes emojis and any hidden non-text symbols
    clean_text = raw_text.encode('ascii', 'ignore').decode('ascii').strip()
    return clean_text

def wrap_caption(text, max_chars):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = (current + " " + word).strip()
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    
    return lines

def ffmpeg_filter_escape_path(path):
    return os.path.abspath(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(" ", "\\ ")

def resolve_bold_fontfile():
    candidates = [
        os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts", "arialbd.ttf"),
        os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts", "ARIALBD.TTF"),
    ]
    for p in candidates:
        if os.path.exists(p): return p
    return None

def generate_video(index):
    try:
        raw_caption = pick_hook_caption()
        caption_lines = wrap_caption(raw_caption, CAPTION_WRAP_CHARS)
        
        if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR, exist_ok=True)

        all_videos = [f for f in os.listdir(VIDEO_DIR) if f.endswith((".mp4", ".mov"))]
        all_audio = [f for f in os.listdir(AUDIO_DIR) if f.endswith((".mp3", ".wav"))]
        
        selected_video = os.path.join(VIDEO_DIR, random.choice(all_videos))
        selected_audio = os.path.join(AUDIO_DIR, random.choice(all_audio))
        output_name = os.path.join(OUTPUT_DIR, f"money_clip_{index+1:02d}.mp4")

        font_esc = ffmpeg_filter_escape_path(resolve_bold_fontfile())

        caption_lines = [line.strip() for line in caption_lines if line.strip()]
        if not caption_lines:
            caption_lines = ["The Grind Never Stops"]

        font_size = 60
        line_spacing = 15
        line_h = font_size + line_spacing
        y0 = f"(h/2)-({line_h}*({len(caption_lines)}-1))/2"

        draw_filters = []
        temp_files = []
        for i, line in enumerate(caption_lines):
            cap_path = os.path.join(OUTPUT_DIR, f"_cap_{os.getpid()}_{index+1:02d}_{i+1:02d}.txt")
            with open(cap_path, "w", encoding="utf-8", newline="") as f:
                f.write(line)
            temp_files.append(cap_path)
            cap_esc = ffmpeg_filter_escape_path(cap_path)
            draw_filters.append(
                "drawtext="
                f"fontfile='{font_esc}':"
                f"textfile='{cap_esc}':reload=0:"
                "x=(w-text_w)/2:"
                f"y=({y0})+({i}*{line_h})-(text_h/2):"
                "fontcolor=white:bordercolor=black:borderw=4:"
                f"fontsize={font_size}:fix_bounds=1:text_shaping=1:expansion=none"
            )

        drawtext = ",".join(draw_filters)

        cmd = [
            resolve_ffmpeg_exe(), "-y", "-loglevel", "error",
            "-stream_loop", "-1", "-i", selected_video,
            "-stream_loop", "-1", "-i", selected_audio,
            "-t", str(VIDEO_DURATION), "-vf", drawtext,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-map", "0:v", "-map", "1:a", output_name
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
        return f"✅ Created: {os.path.basename(output_name)}"
    except Exception as e:
        return f"❌ Error: {e}"

if __name__ == "__main__":
    print(f"🎬 Creating 10 money clips (No-Box Edition)...")
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(generate_video, i) for i in range(10)]
        for f in as_completed(futures):
            print(f.result())
    print("✨ Complete.")

import os
import subprocess
import random
import shutil
from multiprocessing import Pool

# --- FIXED CONFIG ---
# Get the directory where the script is actually running
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Use relative names (no leading slashes)
VIDEO_DIR = os.path.join(BASE_DIR, "aesthetic_loops")
AUDIO_DIR = os.path.join(BASE_DIR, "music_source")
OUTPUT_DIR = os.path.join(BASE_DIR, "batch_output")

VIDEO_DURATION = 60
THREADS = 4


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

        selected_video = os.path.join(VIDEO_DIR, random.choice(all_videos))
        selected_audio = os.path.join(AUDIO_DIR, random.choice(all_audio))

        audio_name = os.path.splitext(os.path.basename(selected_audio))[0]
        output_name = os.path.join(
            OUTPUT_DIR, f"{audio_name}_clip_{index+1:02d}.mp4")

        h_flip = random.choice([True, False])

        # 2. FFmpeg Command
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
            output_name
        ]

        if h_flip:
            cmd.insert(-1, '-vf')
            cmd.insert(-1, 'hflip')

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

    print(f"🎬 Processing 10 videos from MP3/WAV sources...")
    # This will help you verify the path in the terminal
    print(f"📂 Looking in: {VIDEO_DIR}")

    with Pool(THREADS) as p:
        results = p.map(generate_video, range(10))
        for r in results:
            print(r)

    print(f"\n✨ Batch complete.")

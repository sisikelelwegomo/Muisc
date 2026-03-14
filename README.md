# Muisc

Generate short looping videos by combining a random clip from `aesthetic_loops/` with a random audio file from `music_source/` using FFmpeg.

## Run

```powershell
python main_script.py
```

FFmpeg must be available via one of:

- `FFMPEG_PATH` environment variable pointing to `ffmpeg.exe`
- `.\ffmpeg\bin\ffmpeg.exe` inside this project
- `ffmpeg` on your system `PATH`

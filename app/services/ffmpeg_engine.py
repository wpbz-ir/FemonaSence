from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.media_config import MediaSettings
from app.services.media_profiles import TranscodeProfile


class FFmpegError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _run(*args: str, cwd: Path | None = None, timeout: float = 120.0) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        raise FFmpegError(
            "FFMPEG_TIMEOUT",
            f"process did not finish within {int(timeout)} seconds",
        ) from None
    return process.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


async def probe(settings: MediaSettings, input_path: Path) -> dict:
    code, stdout, stderr = await _run(
        settings.ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    )
    if code != 0:
        raise FFmpegError("FFPROBE_FAILED", stderr[-4000:] or "ffprobe failed")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("FFPROBE_INVALID_JSON", "ffprobe returned invalid JSON") from exc

    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    if not videos:
        raise FFmpegError("NO_VIDEO_STREAM", "Input contains no video stream")
    duration = float((data.get("format") or {}).get("duration") or videos[0].get("duration") or 0)
    if duration <= 0:
        raise FFmpegError("DURATION_UNKNOWN", "Input duration could not be determined")
    return {
        "duration": duration,
        "video": {
            "width": int(videos[0].get("width") or 0),
            "height": int(videos[0].get("height") or 0),
            "codec": videos[0].get("codec_name"),
            "pix_fmt": videos[0].get("pix_fmt"),
        },
        "audio_count": len([s for s in streams if s.get("codec_type") == "audio"]),
        "stream_count": len(streams),
    }


async def transcode(settings: MediaSettings, input_path: Path, output_path: Path, profile: TranscodeProfile, source_height: int) -> dict:
    if source_height <= 0:
        raise FFmpegError("SOURCE_HEIGHT_UNKNOWN", "Source video height is not known")
    target_height = min(int(profile.height), int(source_height))
    scale_expr = f"-2:{target_height}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".partial.mp4")
    if temp_path.exists():
        temp_path.unlink()

    args = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-y",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", settings.x264_preset,
        "-crf", str(profile.crf),
        "-maxrate", profile.maxrate,
        "-bufsize", profile.bufsize,
        "-vf", f"scale={scale_expr}",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", settings.audio_bitrate,
        "-ac", "2",
        "-movflags", "+faststart",
        "-map_metadata", "-1",
        "-progress", "pipe:1",
        "-nostats",
        str(temp_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    last_error = ""
    async def consume_stderr():
        nonlocal last_error
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            last_error = line.decode("utf-8", "replace").strip()

    stderr_task = asyncio.create_task(consume_stderr())
    out_time_ms = 0
    timeout_seconds = float(getattr(settings, "ffmpeg_timeout_seconds", 21600))
    code: int | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", "replace").strip()
                if decoded.startswith("out_time_ms="):
                    try:
                        out_time_ms = int(decoded.split("=", 1)[1])
                    except ValueError:
                        pass
            await stderr_task
            code = await process.wait()
    except TimeoutError:
        stderr_task.cancel()
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        temp_path.unlink(missing_ok=True)
        raise FFmpegError(
            "FFMPEG_TIMEOUT",
            f"ffmpeg did not finish within {int(timeout_seconds)} seconds",
        ) from None
    if code != 0 or not temp_path.exists() or temp_path.stat().st_size < 1024:
        temp_path.unlink(missing_ok=True)
        raise FFmpegError("FFMPEG_FAILED", last_error or f"ffmpeg exited with code {code}")

    temp_path.replace(output_path)
    return {"size_bytes": output_path.stat().st_size, "output_progress_ms": out_time_ms}

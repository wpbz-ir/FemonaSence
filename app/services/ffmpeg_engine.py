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
            f"فرایند بیش از {int(timeout)} ثانیه طول کشید و متوقف شد.",
        ) from None
    except asyncio.CancelledError:
        # [FIX] Outer cancellation (worker shutdown / task cancel) must not
        # leak the child process: kill it and re-raise so cancellation
        # semantics are preserved.
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        raise
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
        # Persian-first prefix so the panel toast is readable even when the
        # ffprobe stderr tail (kept as diagnostic detail) is English.
        detail = stderr[-4000:].strip()
        raise FFmpegError(
            "FFPROBE_FAILED",
            f"ffprobe شکست خورد. {detail}" if detail else "ffprobe شکست خورد.",
        )
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("FFPROBE_INVALID_JSON", "خروجی ffprobe نامعتبر است.") from exc

    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    if not videos:
        raise FFmpegError("NO_VIDEO_STREAM", "ورودی هیچ استریم ویدیویی ندارد.")
    # [FIX] ffprobe reports "N/A" (or other non-numeric junk) for duration on
    # some containers; float() must not leak a bare ValueError — treat it as
    # the same DURATION_UNKNOWN failure as a missing duration.
    try:
        duration = float((data.get("format") or {}).get("duration") or videos[0].get("duration") or 0)
    except (TypeError, ValueError):
        raise FFmpegError("DURATION_UNKNOWN", "مدت ویدیو قابل تشخیص نبود.") from None
    if duration <= 0:
        raise FFmpegError("DURATION_UNKNOWN", "مدت ویدیو قابل تشخیص نبود.")
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
        raise FFmpegError("SOURCE_HEIGHT_UNKNOWN", "ارتفاع ویدیوی منبع مشخص نیست.")
    # [FIX] Even-height cap: when the source is smaller than the profile the
    # minimum could land on an odd value (e.g. 479); scale=-2:479 keeps that
    # odd height and libx264/yuv420p then fails on EVERY odd-height video.
    # Floor to an even number so the filter never receives an odd target.
    target_height = (min(int(profile.height), int(source_height)) // 2) * 2
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
        # Reap the cancelled stderr reader so no unretrieved-exception
        # warning is emitted when the task object is garbage collected.
        await asyncio.gather(stderr_task, return_exceptions=True)
        temp_path.unlink(missing_ok=True)
        raise FFmpegError(
            "FFMPEG_TIMEOUT",
            f"ffmpeg بیش از {int(timeout_seconds)} ثانیه طول کشید و متوقف شد.",
        ) from None
    except asyncio.CancelledError:
        # [FIX] Outer task cancellation (worker shutdown) previously skipped
        # every cleanup: the ffmpeg child kept running and the .partial.mp4
        # file stayed on disk. Kill the child, reap the stderr reader, unlink
        # the partial output, then re-raise so cancellation still propagates.
        stderr_task.cancel()
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        await asyncio.gather(stderr_task, return_exceptions=True)
        temp_path.unlink(missing_ok=True)
        raise
    if code != 0 or not temp_path.exists() or temp_path.stat().st_size < 1024:
        temp_path.unlink(missing_ok=True)
        # Persian-first message with the exit code; the ffmpeg stderr tail
        # (last_error) stays as a diagnostic detail after it.
        message = f"ffmpeg با کد خطای {code} خاتمه یافت."
        if last_error:
            message = f"{message} {last_error}"
        raise FFmpegError("FFMPEG_FAILED", message)

    temp_path.replace(output_path)
    return {"size_bytes": output_path.stat().st_size, "output_progress_ms": out_time_ms}

"""YouTube video download and metadata extraction using yt-dlp.

Turns a YouTube URL into a local MP4 file ready for the Whisper transcription pipeline.

Features:
  - Duration capping (stops a 3-hour podcast from overloading CPU transcription)
  - Quality selection (720p/best MP4 to balance download speed with visual quality)
  - Seamless integration with the bundled imageio-ffmpeg binary
  - Metadata extraction (title, duration, channel, clean take ID generation)
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import yt_dlp

_YOUTUBE_PATTERN = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/|v/)|youtu\.be/)([\w-]{11})([?&].*)?$",
    re.IGNORECASE,
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9]+")


class YouTubeError(Exception):
    """Raised when YouTube metadata extraction or download fails."""


def is_youtube_url(url: str) -> bool:
    """Checks if a string is a valid YouTube video or shorts URL."""
    if not url or not isinstance(url, str):
        return False
    return bool(_YOUTUBE_PATTERN.match(url.strip()))


def extract_video_id(url: str) -> str:
    """Extracts the 11-character YouTube video ID."""
    match = _YOUTUBE_PATTERN.match((url or "").strip())
    if not match:
        raise YouTubeError(f"Not a recognizable YouTube URL: {url!r}")
    return match.group(5)


def sanitize_label(text: str, max_len: int = 32) -> str:
    """Turns arbitrary text into a safe ASCII take ID token."""
    cleaned = _SAFE_ID.sub("_", (text or "").strip()).strip("_")[:max_len]
    return cleaned.upper() if cleaned else "YT_TAKE"


def _get_cookie_file() -> str | None:
    """Finds or decodes YouTube cookies if provided via file or environment."""
    env_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if env_file and Path(env_file).is_file():
        return env_file

    for candidate in [Path("cookies.txt"), Path("/tmp/cookies.txt"), Path("/app/cookies.txt")]:
        if candidate.is_file():
            return str(candidate.resolve())

    raw_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if raw_cookies:
        try:
            decoded = base64.b64decode(raw_cookies).decode("utf-8", errors="ignore")
            if "# Netscape" in decoded or "\t" in decoded:
                raw_cookies = decoded
        except Exception:
            pass

        target = Path(tempfile.gettempdir()) / "yt_runtime_cookies.txt"
        target.write_text(raw_cookies, encoding="utf-8")
        return str(target)

    return None


def _build_ydl_opts(extra_opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Builds base yt-dlp options configured to bypass bot detection on datacenter IPs."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "mweb", "web"],
            }
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    proxy = os.environ.get("YOUTUBE_PROXY") or os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    cookie_file = _get_cookie_file()
    if cookie_file:
        opts["cookiefile"] = cookie_file

    if extra_opts:
        opts.update(extra_opts)
    return opts


def _get_info_oembed(url: str) -> dict[str, Any]:
    """Fallback metadata extractor using YouTube official oEmbed API (immune to bot walls)."""
    import json
    import urllib.request

    video_id = extract_video_id(url)
    try:
        req_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(
            req_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "id": video_id,
            "title": data.get("title", f"YouTube {video_id}"),
            "uploader": data.get("author_name", "YouTube"),
            "duration": 0.0,
            "description": "",
            "thumbnail": data.get("thumbnail_url", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
            "is_live": False,
        }
    except Exception:
        return {
            "id": video_id,
            "title": f"YouTube {video_id}",
            "uploader": "YouTube",
            "duration": 0.0,
            "description": "",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "is_live": False,
        }


def get_info(url: str) -> dict[str, Any]:
    """Extracts metadata without downloading the media, immune to bot detection walls."""
    if not is_youtube_url(url):
        raise YouTubeError(f"Invalid YouTube URL: {url!r}")

    # First attempt: extract_flat=True (avoids player API bot checks)
    opts = _build_ydl_opts({
        "skip_download": True,
        "extract_flat": True,
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=False)
            if info:
                duration = float(info.get("duration") or 0)
                return {
                    "id": info.get("id") or extract_video_id(url),
                    "title": info.get("title", "YouTube Video"),
                    "uploader": info.get("uploader") or info.get("channel") or "",
                    "duration": duration,
                    "description": info.get("description", "")[:500] if info.get("description") else "",
                    "thumbnail": info.get("thumbnail", ""),
                    "is_live": bool(info.get("is_live")),
                }
    except Exception:
        pass

    # Second attempt: try oEmbed fallback so metadata extraction never blocks ingest initiation
    try:
        return _get_info_oembed(url)
    except Exception as error:
        raise YouTubeError(f"YouTube metadata extraction failed: {error}") from error


def download(
    url: str,
    output_dir: Path,
    *,
    filename_stem: str,
    max_duration_s: int = 180,
) -> tuple[Path, dict[str, Any]]:
    """Downloads a YouTube video to output_dir as an MP4 file.

    Returns (target_path, metadata_dict).
    If the video exceeds max_duration_s, it downloads or trims to the first max_duration_s.
    """
    info = get_info(url)
    if info.get("is_live"):
        raise YouTubeError("Live streams are not supported for transcription.")

    output_dir.mkdir(parents=True, exist_ok=True)
    target_mp4 = output_dir / f"{filename_stem}.mp4"

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()

    # Use a temporary directory for the raw download/assembly
    with tempfile.TemporaryDirectory() as tmp_dir:
        staging_template = Path(tmp_dir) / "download.%(ext)s"

        ydl_opts = _build_ydl_opts({
            "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "ffmpeg_location": ffmpeg_bin,
            "outtmpl": str(staging_template),
            "merge_output_format": "mp4",
        })

        duration = info.get("duration", 0.0)
        needs_trim = duration > max_duration_s > 0

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url.strip()])
        except Exception as error:
            raise YouTubeError(f"Download failed: {error}") from error

        # Find the produced file in tmp_dir
        candidates = list(Path(tmp_dir).glob("download.*"))
        if not candidates:
            raise YouTubeError("yt-dlp completed but no output file was produced.")

        staging_file = candidates[0]

        # If it needs trimming, trim with ffmpeg
        if needs_trim:
            trimmed_staging = Path(tmp_dir) / "trimmed.mp4"
            cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0",
                "-t",
                str(max_duration_s),
                "-i",
                str(staging_file),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                str(trimmed_staging),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and trimmed_staging.is_file():
                staging_file = trimmed_staging
            duration = min(duration, float(max_duration_s))

        # Copy final to target
        shutil.copy2(staging_file, target_mp4)

    return target_mp4, {
        "title": info["title"],
        "uploader": info["uploader"],
        "duration": duration,
        "video_id": info["id"],
    }

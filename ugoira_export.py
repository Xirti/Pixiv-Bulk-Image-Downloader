from __future__ import annotations

import io
import os
import re
import threading
import subprocess
import tempfile
import time
import zipfile
import zlib
from contextlib import closing
from pathlib import Path

from pixiv_adapter import PixivPolicyError

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_FRAME_PIXELS = 16_000_000
MAX_GIF_MEMORY = 256 * 1024 * 1024
MAX_CONVERSION_SECONDS = 120
_CONVERSION_SLOT = threading.BoundedSemaphore(1)


class _Output(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_ARCHIVE_BYTES:
            raise PixivPolicyError("转换文件超过 40MB，请选择标准清晰度或原始帧 ZIP")
        return super().write(data)


def _decoded_frames(raw: bytes, frames: list[dict]):
    from PIL import Image

    if len(raw) > MAX_ARCHIVE_BYTES or not isinstance(frames, list) or not 1 <= len(frames) <= 1500:
        raise PixivPolicyError("动图超出转换范围，仍可下载原始帧 ZIP")
    total_delay = 0
    for frame in frames:
        if (not isinstance(frame, dict)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png)", str(frame.get("file") or ""), re.I)
                or not isinstance(frame.get("delay"), int) or isinstance(frame["delay"], bool)
                or not 1 <= frame["delay"] <= 60000):
            raise PixivPolicyError("动图帧名称或时长无效")
        total_delay += frame["delay"]
    if total_delay > 600000:
        raise PixivPolicyError("动图超过 10 分钟，请下载原始帧 ZIP")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) > 1500 or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES):
            raise PixivPolicyError("动图帧包超出解压范围")
        for entry in entries:
            if (entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or entry.file_size > MAX_FRAME_BYTES or entry.compress_size > MAX_FRAME_BYTES):
                raise PixivPolicyError("动图帧包包含不支持或过大的图片帧")
        size = None
        for frame in frames:
            # Never extract remote names as paths. ZIP CRC and the decoded image
            # are checked before any output enters the download transaction.
            with Image.open(io.BytesIO(archive.read(frame["file"])), formats=("JPEG", "PNG")) as image:
                if image.width * image.height > MAX_FRAME_PIXELS:
                    raise PixivPolicyError("动图单帧分辨率过大，请选择标准清晰度")
                if size is not None and image.size != size:
                    raise PixivPolicyError("动图帧尺寸不一致")
                size = image.size
                rgba = image.convert("RGBA")
            try:
                yield rgba, frame["delay"]
            finally:
                rgba.close()


def _gif(raw, frames) -> bytes:
    from PIL import Image

    images, durations = [], []
    pixels = largest = elapsed = rounded = 0
    deadline = time.monotonic() + MAX_CONVERSION_SECONDS
    try:
        with closing(_decoded_frames(raw, frames)) as decoded:
            for image, delay in decoded:
                if time.monotonic() >= deadline:
                    raise PixivPolicyError("GIF 转换超出处理预算，请选择标准清晰度或原始帧 ZIP")
                frame_pixels = image.width * image.height
                pixels += frame_pixels
                largest = max(largest, frame_pixels)
                if pixels * 3 + largest * 8 > MAX_GIF_MEMORY:
                    raise PixivPolicyError("GIF 转换超出内存预算，请选择标准清晰度或原始帧 ZIP")
                with Image.new("RGBA", image.size, "white") as background:
                    background.alpha_composite(image)
                    with background.convert("RGB") as rgb:
                        palette = rgb.quantize(colors=255)
                images.append(palette)
                with image.getchannel("A") as alpha:
                    with alpha.point(lambda value: 255 if value < 128 else 0) as mask:
                        palette.paste(255, mask=mask)
                palette.info["transparency"] = 255
                # GIF stores hundredths of a second. Round the cumulative
                # timeline so rounding errors do not grow on every frame.
                elapsed += delay
                next_rounded = max(rounded + 10, ((elapsed + 5) // 10) * 10)
                durations.append(next_rounded - rounded)
                rounded = next_rounded
        with _Output() as output:
            images[0].save(output, format="GIF", save_all=True, append_images=images[1:],
                           duration=durations, loop=0, disposal=2, transparency=255,
                           background=255, optimize=False)
            if time.monotonic() >= deadline:
                raise PixivPolicyError("GIF 编码超时，请选择标准清晰度或原始帧 ZIP")
            return output.getvalue()
    finally:
        for image in images:
            image.close()


def find_ffmpeg() -> str | None:
    """Use an explicitly registered local PATH entry, never implicit cwd."""
    filename = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        folder = Path(entry.strip('"'))
        if not entry or not folder.is_absolute() or folder.drive.startswith("\\\\"):
            continue
        try:
            candidate = (folder / filename).resolve(strict=True)
            if candidate.is_file() and not candidate.drive.startswith("\\\\"):
                return str(candidate)
        except (OSError, RuntimeError):
            continue
    return None


def export_formats() -> dict[str, bool]:
    return {"gif": True, "mp4": find_ffmpeg() is not None}


def require_mp4_encoder() -> str:
    encoder = find_ffmpeg()
    if encoder is None:
        raise PixivPolicyError("未检测到本机 FFmpeg，MP4 导出需要将 FFmpeg 加入 PATH 后重启 MOKU；也可选择 GIF 或原始帧 ZIP")
    return encoder


def _mp4(raw, frames, encoder) -> bytes:
    from PIL import Image

    deadline = time.monotonic() + MAX_CONVERSION_SECONDS
    with tempfile.TemporaryDirectory(prefix="moku-encode-") as temporary:
        root = Path(temporary)
        lines = ["ffconcat version 1.0"]
        total_pixels = total_bytes = 0
        with closing(_decoded_frames(raw, frames)) as decoded:
            for index, (image, delay) in enumerate(decoded):
                total_pixels += image.width * image.height
                if total_pixels > 256_000_000 or time.monotonic() >= deadline:
                    raise PixivPolicyError("MP4 转换超出处理预算，请选择标准清晰度或原始帧 ZIP")
                name = f"frame{index:06d}.png"
                with Image.new("RGBA", image.size, "white") as background:
                    background.alpha_composite(image)
                    with background.convert("RGB") as rgb:
                        rgb.save(root / name, format="PNG", compress_level=1)
                total_bytes += (root / name).stat().st_size
                if total_bytes > 256 * 1024 * 1024:
                    raise PixivPolicyError("MP4 转换暂存超出预算，请选择标准清晰度或原始帧 ZIP")
                lines.extend([f"file '{name}'", "option framerate 1000", f"duration {delay / 1000:.3f}"])
        # The final repeated image gives the last original frame its complete
        # duration. Input timestamps use milliseconds, not an assumed 25 fps.
        lines.extend([f"file '{name}'", "option framerate 1000"])
        (root / "frames.ffconcat").write_text("\n".join(lines) + "\n", encoding="ascii")
        try:
            result = subprocess.run(
                [encoder, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                 "-f", "concat", "-safe", "0", "-protocol_whitelist", "file", "-i", "frames.ffconcat",
                 "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white,format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2",
                 "-fps_mode", "vfr", "-enc_time_base", "1:1000", "-video_track_timescale", "1000",
                 "-movflags", "+faststart", "-f", "mp4", "animation.mp4"],
                cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=max(.1, deadline - time.monotonic()),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PixivPolicyError("MP4 编码超时，请选择标准清晰度或原始帧 ZIP") from exc
        if result.returncode != 0:
            raise PixivPolicyError("MP4 编码失败，请使用支持 H.264（libx264）的 FFmpeg，或选择 GIF／原始帧 ZIP")
        output = root / "animation.mp4"
        if output.is_symlink() or not output.is_file():
            raise PixivPolicyError("MP4 编码未生成有效文件")
        with output.open("rb") as stream:
            encoded = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(encoded) > MAX_ARCHIVE_BYTES:
            raise PixivPolicyError("转换文件超过 40MB，请选择标准清晰度或原始帧 ZIP")
        if encoded[4:8] != b"ftyp":
            raise PixivPolicyError("MP4 编码未生成有效文件")
        return encoded


def export_ugoira(raw: bytes, frames: list[dict], image_format: str) -> bytes:
    if image_format not in {"gif", "mp4"}:
        raise PixivPolicyError("动图转换格式无效")
    encoder = require_mp4_encoder() if image_format == "mp4" else None
    from PIL import Image

    if not _CONVERSION_SLOT.acquire(blocking=False):
        raise PixivPolicyError("已有动图正在转换，请稍后重试")
    try:
        return _mp4(raw, frames, encoder) if image_format == "mp4" else _gif(raw, frames)
    except PixivPolicyError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zlib.error,
            zipfile.BadZipFile, Image.DecompressionBombError) as exc:
        raise PixivPolicyError("动图图片帧已损坏或不支持转换") from exc
    finally:
        _CONVERSION_SLOT.release()

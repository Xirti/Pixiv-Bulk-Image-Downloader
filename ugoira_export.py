from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
import zlib
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from pixiv_adapter import PixivPolicyError

def _configured_limit(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 {low} 到 {high} 之间的整数") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low} 到 {high} 之间的整数")
    return value


MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = _configured_limit("MOKU_UGOIRA_MAX_MIB", 512, 40, 4096) * 1024 * 1024
MAX_EXPANDED_BYTES = _configured_limit("MOKU_UGOIRA_TEMP_MIB", 2048, 128, 16384) * 1024 * 1024
MAX_FRAME_PIXELS = 16_000_000
MAX_GIF_MEMORY = 256 * 1024 * 1024
MAX_CONVERSION_SECONDS = _configured_limit("MOKU_UGOIRA_TIMEOUT_SECONDS", 600, 30, 3600)
DISK_RESERVE_BYTES = 64 * 1024 * 1024
_CONVERSION_SLOT = threading.BoundedSemaphore(1)


class UgoiraExportError(PixivPolicyError):
    def __init__(self, message: str, code: str = "conversion_failed"):
        super().__init__(message)
        self.code = code
        self.artwork_id = ""


class UgoiraCancelledError(PixivPolicyError):
    pass


def _frames(raw: bytes | BinaryIO, frames: list[dict], check, *, preflight=None):
    """Validate the archive once, then hold only the current encoded/decoded frame."""
    from PIL import Image

    if not isinstance(frames, list) or not 1 <= len(frames) <= 1500:
        raise UgoiraExportError("动图帧数超出转换范围，请选择原始帧 ZIP", "frame_limit")
    total_delay = 0
    for frame in frames:
        if (not isinstance(frame, dict)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png)", str(frame.get("file") or ""), re.I)
                or type(frame.get("delay")) is not int or not 1 <= frame["delay"] <= 60000):
            raise UgoiraExportError("动图帧名称或时长无效", "invalid_frames")
        total_delay += frame["delay"]
    if total_delay > 600000:
        raise UgoiraExportError("动图超过 10 分钟，请选择原始帧 ZIP", "duration_limit")
    source = io.BytesIO(raw) if isinstance(raw, bytes) else raw
    source.seek(0, os.SEEK_END)
    if source.tell() > MAX_ARCHIVE_BYTES:
        raise UgoiraExportError(f"动图帧包超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB，请分开处理", "archive_limit")
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if (len(entries) > 1500 or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES):
            raise UgoiraExportError("动图帧包超出解压范围，请选择原始帧 ZIP", "archive_limit")
        for entry in entries:
            if (entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or entry.file_size > MAX_FRAME_BYTES or entry.compress_size > MAX_FRAME_BYTES):
                raise UgoiraExportError("动图帧包包含不支持或过大的图片帧", "frame_limit")
        size = None
        for frame in frames:
            check()
            encoded = archive.read(frame["file"])
            # Read by ZIP entry name, never extract remote paths. CRC and image
            # decoding must both succeed before an output can be published.
            with Image.open(io.BytesIO(encoded), formats=("JPEG", "PNG")) as image:
                if image.width * image.height > MAX_FRAME_PIXELS:
                    raise UgoiraExportError("动图单帧分辨率过大，请选择标准或原始帧 ZIP", "frame_limit")
                if size is not None and image.size != size:
                    raise UgoiraExportError("动图帧尺寸不一致", "invalid_frames")
                size = image.size
                if preflight is not None:
                    preflight(size)
                image.load()
                yield image, encoded, frame["delay"]


class _Output:
    def __init__(self, stream: BinaryIO, check):
        self.stream = stream
        self.check = check

    def write(self, data):
        self.check()
        if self.stream.tell() + len(data) > MAX_ARCHIVE_BYTES:
            raise UgoiraExportError(f"转换文件超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB，请选择其他格式或原始帧 ZIP", "output_limit")
        return self.stream.write(data)


def _gif(raw, frames, output, check):
    from PIL import GifImagePlugin, Image

    def check_memory(size):
        if size[0] * size[1] * 28 > MAX_GIF_MEMORY:
            raise UgoiraExportError("GIF 单帧转换需要的内存过多，请选择 MP4、标准或原始帧 ZIP", "memory_limit")

    elapsed = rounded = 0
    first = True
    pending = None
    signature = None
    duration = 0

    def emit(palette, delay):
        nonlocal first
        if first:
            header, _ = GifImagePlugin.getheader(palette, info={
                "loop": 0, "transparency": 255, "background": 255,
            })
            for block in header:
                output.write(block)
            first = False
        for block in GifImagePlugin.getdata(palette, duration=delay,
                disposal=2, transparency=255, include_color_table=True):
            output.write(block)

    try:
        with closing(_frames(raw, frames, check, preflight=check_memory)) as decoded:
            for image, _encoded, delay in decoded:
                if delay < 10:
                    raise UgoiraExportError("GIF 无法准确保存小于 10ms 的帧时长，请选择 MP4 或原始帧 ZIP", "gif_timing")
                elapsed += delay
                next_rounded = max(rounded + 10, ((elapsed + 5) // 10) * 10)
                frame_delay = next_rounded - rounded
                rounded = next_rounded
                with image.convert("RGBA") as rgba:
                    current_signature = hashlib.sha256(rgba.tobytes()).digest()
                    if pending is not None and current_signature == signature:
                        # One pending palette coalesces a still pose without
                        # retaining every frame or quantizing identical pixels.
                        duration += frame_delay
                        continue
                    if pending is not None:
                        emit(pending, duration)
                        pending.close()
                        pending = None
                    with Image.new("RGBA", image.size, "white") as background:
                        background.alpha_composite(rgba)
                        with background.convert("RGB") as rgb:
                            pending = rgb.quantize(colors=255)
                    with rgba.getchannel("A") as alpha:
                        with alpha.point(lambda value: 255 if value < 128 else 0) as mask:
                            pending.paste(255, mask=mask)
                    pending.info["transparency"] = 255
                    signature = current_signature
                    duration = frame_delay
        emit(pending, duration)
        output.write(b";")
    finally:
        if pending is not None:
            pending.close()


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
        raise UgoiraExportError("未检测到本机 FFmpeg，MP4 导出需要将 FFmpeg 加入 PATH 后重启 MOKU；也可选择 GIF 或原始帧 ZIP", "missing_encoder")
    return encoder


def _check_frame_space(root: Path, total_bytes: int, needed: int) -> None:
    if total_bytes + needed > MAX_EXPANDED_BYTES:
        raise UgoiraExportError(f"MP4 中间帧超过 {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB，请选择原始帧 ZIP", "temporary_limit")
    if shutil.disk_usage(root).free < needed + DISK_RESERVE_BYTES:
        raise UgoiraExportError("MP4 转换临时磁盘空间不足，请释放空间后继续", "disk_space")


def _mp4(raw, frames, encoder, output, check):
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="moku-encode-") as temporary:
        root = Path(temporary)
        lines = ["ffconcat version 1.0"]
        total_bytes = 0
        names = {}
        with closing(_frames(raw, frames, check)) as decoded:
            for index, (image, encoded, delay) in enumerate(decoded):
                remote_name = frames[index]["file"]
                name = names.get(remote_name)
                if name is None:
                    # Keep JPEG and opaque PNG frames verbatim. Re-encoding
                    # JPEGs to PNG caused the previous 256 MiB staging failure.
                    alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                    name = f"frame{index:06d}." + ("jpg" if image.format == "JPEG" else "png")
                    needed = len(encoded) if not alpha else image.width * image.height * 4 + 65536
                    _check_frame_space(root, total_bytes, needed)
                    if alpha:
                        with image.convert("RGBA") as rgba:
                            with Image.new("RGBA", image.size, "white") as background:
                                background.alpha_composite(rgba)
                                with background.convert("RGB") as rgb:
                                    rgb.save(root / name, format="PNG", compress_level=1)
                    else:
                        (root / name).write_bytes(encoded)
                    total_bytes += (root / name).stat().st_size
                    names[remote_name] = name
        if len({Path(name).suffix for name in names.values()}) > 1:
            # ffconcat requires one codec across segments. Only mixed archives
            # normalize JPEGs; the common all-JPEG path keeps original bytes.
            for remote_name, name in names.items():
                if Path(name).suffix != ".jpg":
                    continue
                check()
                path = root / name
                previous_size = path.stat().st_size
                normalized = path.with_suffix(".png")
                with Image.open(path) as image:
                    _check_frame_space(root, total_bytes, image.width * image.height * 4 + 65536)
                    with image.convert("RGB") as rgb:
                        rgb.save(normalized, format="PNG", compress_level=1)
                total_bytes += normalized.stat().st_size - previous_size
                path.unlink()
                names[remote_name] = normalized.name
        for frame in frames:
            name = names[frame["file"]]
            lines.extend([f"file '{name}'", "option framerate 1000", f"duration {frame['delay'] / 1000:.3f}"])
        lines.extend([f"file '{name}'", "option framerate 1000"])
        (root / "frames.ffconcat").write_text("\n".join(lines) + "\n", encoding="ascii")
        # stderr goes to a file so a noisy/broken encoder cannot grow memory or
        # block on a full pipe. Always stop and reap it before temporary cleanup.
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [encoder, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                 "-f", "concat", "-safe", "0", "-protocol_whitelist", "file", "-i", "frames.ffconcat",
                 "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white,format=yuv420p",
                 "-c:v", "libx264", "-bf", "0", "-preset", "veryfast", "-crf", "18", "-threads", "2",
                 "-fps_mode", "vfr", "-enc_time_base", "1:1000", "-video_track_timescale", "1000",
                 "-progress", "encode.progress", "-stats_period", "1",
                 "-fs", str(MAX_ARCHIVE_BYTES + 1),
                 "-movflags", "+faststart", "-f", "mp4", "animation.mp4"],
                cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=errors, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                while process.poll() is None:
                    check()
                    if shutil.disk_usage(root).free < DISK_RESERVE_BYTES:
                        raise UgoiraExportError("MP4 编码临时磁盘空间不足，请释放空间后继续", "disk_space")
                    time.sleep(.05)
                check()
                movie = root / "animation.mp4"
                if movie.is_file() and movie.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise UgoiraExportError(f"转换文件超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB，请选择其他格式或原始帧 ZIP", "output_limit")
                if process.returncode != 0:
                    raise UgoiraExportError("MP4 编码失败，请使用支持 H.264（libx264）的 FFmpeg，或选择 GIF／原始帧 ZIP", "encoder_failed")
                if movie.is_symlink() or not movie.is_file():
                    raise UgoiraExportError("MP4 编码未生成有效文件", "encoder_failed")
                progress = root / "encode.progress"
                if not progress.is_file():
                    raise UgoiraExportError("MP4 编码未返回完整进度，请重试或选择原始帧 ZIP", "encoder_failed")
                with progress.open("rb") as stream:
                    stream.seek(max(0, progress.stat().st_size - 65536))
                    values = dict(line.split("=", 1) for line in stream.read().decode("ascii", errors="replace").splitlines() if "=" in line)
                # FFmpeg's -fs may stop early with exit code zero. A complete
                # trailer alone does not prove all original frames were encoded.
                if values.get("progress") != "end" or values.get("frame", "").strip() != str(len(frames) + 1):
                    raise UgoiraExportError("MP4 未完成全部帧，可能超过文件上限，请选择其他格式或原始帧 ZIP", "output_limit")
                with movie.open("rb") as stream:
                    if stream.read(8)[4:8] != b"ftyp":
                        raise UgoiraExportError("MP4 编码未生成有效文件", "encoder_failed")
                    stream.seek(0)
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        output.write(chunk)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()


def export_ugoira(raw: bytes | BinaryIO, frames: list[dict], image_format: str, *,
        output: BinaryIO | None = None, cancel_event: threading.Event | None = None) -> bytes | None:
    """Export to a caller-owned file; byte results retain the small-input API."""
    if image_format not in {"gif", "mp4"}:
        raise UgoiraExportError("动图转换格式无效")
    encoder = require_mp4_encoder() if image_format == "mp4" else None
    from PIL import Image

    if not _CONVERSION_SLOT.acquire(blocking=False):
        raise PixivPolicyError("已有动图正在转换，请稍后重试")
    deadline = time.monotonic() + MAX_CONVERSION_SECONDS
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise UgoiraCancelledError("动图转换已取消")
        if time.monotonic() >= deadline:
            raise UgoiraExportError(f"动图转换超过 {MAX_CONVERSION_SECONDS} 秒，请选择其他格式或原始帧 ZIP", "conversion_timeout")
    stream = output if output is not None else io.BytesIO()
    try:
        check()
        writer = _Output(stream, check)
        if image_format == "mp4":
            _mp4(raw, frames, encoder, writer, check)
        else:
            _gif(raw, frames, writer, check)
        check()
        return stream.getvalue() if output is None else None
    except (UgoiraExportError, UgoiraCancelledError):
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise UgoiraExportError("动图转换磁盘空间不足，请释放空间后继续", "disk_space") from exc
        raise UgoiraExportError("动图图片帧无法读取或转换文件无法写入，请检查文件与磁盘", "conversion_io") from exc
    except (ValueError, KeyError, RuntimeError, zlib.error,
            zipfile.BadZipFile, Image.DecompressionBombError) as exc:
        raise UgoiraExportError("动图图片帧已损坏或不支持转换", "invalid_frames") from exc
    finally:
        if output is None:
            stream.close()
        _CONVERSION_SLOT.release()

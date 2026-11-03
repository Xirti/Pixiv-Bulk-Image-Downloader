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
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import ExitStack, closing
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
            with closing(Image.open(io.BytesIO(encoded), formats=("JPEG", "PNG"))) as image:
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


def _image_work_budget(limit: int) -> int:
    """Use a bounded share of current memory headroom for image work."""
    if os.name != "nt":
        return min(limit, MAX_GIF_MEMORY)
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in (
                "total_physical", "available_physical", "total_commit", "available_commit",
                "total_virtual", "available_virtual", "reserved")]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    query = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(MemoryStatus)]
    query.restype = wintypes.BOOL
    if not query(ctypes.byref(status)):
        return 0
    # Leave the majority of available RAM/commit to the OS and other apps.
    available = min(status.available_physical, status.available_commit)
    return min(limit, available // 3)


class _GifPaletteJob:
    """Keep frame inputs and output owned even if executor submission fails."""

    def __init__(self, rgb, mask, buffers, delay, check):
        self.rgb = rgb
        self.mask = mask
        self.buffers = buffers
        self.delay = delay
        self.check = check
        self.palette = None
        self.future = Future()

    def run(self):
        with self.buffers:
            self.check()
            self.palette = self.rgb.quantize(colors=255)
            self.check()
            if self.mask is not None:
                self.palette.paste(255, mask=self.mask)
            self.palette.info["transparency"] = 255

    def close(self):
        self.buffers.close()
        if self.palette is not None:
            self.palette.close()
            self.palette = None


def _gif(raw, frames, output, check):
    from PIL import GifImagePlugin, Image

    def check_memory(size):
        nonlocal parallel
        memory = size[0] * size[1] * 28
        if memory > MAX_GIF_MEMORY:
            raise UgoiraExportError("GIF 单帧转换需要的内存过多，请选择 MP4、标准或原始帧 ZIP", "memory_limit")
        # Reserve preparation/copy headroom as well as two quantizers. Larger
        # frames retain the existing single-frame path and admission limit.
        parallel = len(frames) > 1 and memory * 4 <= parallel_budget and (os.cpu_count() or 1) > 1

    elapsed = rounded = 0
    first = True
    signature = None
    parallel = False
    parallel_budget = _image_work_budget(MAX_GIF_MEMORY * 4)
    executor = None
    jobs = deque()
    stopped = threading.Event()

    def check_pipeline():
        check()
        if stopped.is_set():
            raise UgoiraCancelledError("动图转换已取消")

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

    def emit_next():
        job = jobs[0]
        while not job.future.done():
            check_pipeline()
            wait((job.future,), timeout=.05)
        check_pipeline()
        job.future.result()
        jobs.popleft()
        with closing(job):
            emit(job.palette, job.delay)

    try:
        with closing(_frames(raw, frames, check, preflight=check_memory)) as decoded:
            for image, _encoded, delay in decoded:
                if delay < 10:
                    raise UgoiraExportError("GIF 无法准确保存小于 10ms 的帧时长，请选择 MP4 或原始帧 ZIP", "gif_timing")
                elapsed += delay
                next_rounded = max(rounded + 10, ((elapsed + 5) // 10) * 10)
                frame_delay = next_rounded - rounded
                rounded = next_rounded
                opaque = image.mode in {"1", "L", "RGB"} and "transparency" not in image.info
                with ExitStack() as buffers:
                    mask = None
                    with closing(image.convert("RGBA")) as rgba:
                        current_signature = hashlib.sha256(rgba.tobytes()).digest()
                        if jobs and current_signature == signature:
                            # Hash the same RGBA pixels across source modes so
                            # identical opaque and alpha frames still coalesce.
                            jobs[-1].delay += frame_delay
                            continue
                        if jobs and not parallel:
                            emit_next()
                        if not opaque:
                            with closing(Image.new("RGBA", image.size, "white")) as background:
                                background.alpha_composite(rgba)
                                rgb = buffers.enter_context(closing(background.convert("RGB")))
                            with closing(rgba.getchannel("A")) as alpha:
                                mask = buffers.enter_context(closing(
                                    alpha.point(lambda value: 255 if value < 128 else 0)))
                    # Release RGBA/compositing buffers before palette building.
                    # Serial RGB sources need no additional copy.
                    if opaque:
                        rgb = image
                        if parallel or image.mode != "RGB":
                            # The decoder owns its image only until next yield;
                            # a worker receives an independent pixel buffer.
                            rgb = buffers.enter_context(closing(image.convert("RGB")))
                    job = _GifPaletteJob(rgb, mask, buffers, frame_delay, check_pipeline)
                    jobs.append(job)
                    job.buffers = buffers.pop_all()
                    if parallel:
                        try:
                            if executor is None:
                                executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="moku-gif")
                            job.future = executor.submit(job.run)
                        except RuntimeError as exc:
                            raise UgoiraExportError("GIF 并行转换线程启动失败，请稍后重试") from exc
                    else:
                        job.run()
                        job.future.set_result(None)
                    signature = current_signature
                if len(jobs) == 2:
                    emit_next()
        while jobs:
            emit_next()
        output.write(b";")
    finally:
        stopped.set()
        if executor is not None:
            # Let every submitted job release its inputs, even after a decode,
            # output or cancellation failure; no worker survives this export.
            executor.shutdown(wait=True)
        for job in jobs:
            job.close()


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


def _mp4_cpu_threads(size, frame_count: int) -> int:
    cores = os.cpu_count() or 1
    # Include encoder reference/lookahead buffers and its fixed runtime cost.
    memory = size[0] * size[1] * 96 + 32 * 1024 * 1024
    if cores >= 4 and frame_count >= 8 and memory <= _image_work_budget(1024 * 1024 * 1024):
        return 4
    return min(2, cores)


def _encode_mp4(root: Path, encoder: str, frame_count: int, check, *, backend: str, threads: int) -> Path:
    movie = root / "animation.mp4"
    progress = root / "encode.progress"
    check()
    # A CPU retry starts with fresh output and progress after GPU cleanup.
    movie.unlink(missing_ok=True)
    progress.unlink(missing_ok=True)
    codec_options = (
        ["-c:v", "libx264", "-bf", "0", "-preset", "veryfast", "-crf", "18", "-threads", str(threads)]
        if backend == "cpu" else
        ["-c:v", "h264_nvenc", "-bf", "0", "-preset", "p4", "-rc", "vbr", "-cq", "18", "-b:v", "0"]
    )
    # File-backed diagnostics keep encoder output bounded by disk headroom.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [encoder, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
             "-threads", "1", "-filter_threads", "1",
             "-f", "concat", "-safe", "0", "-protocol_whitelist", "file", "-noautorotate", "-i", "frames.ffconcat",
             "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white,setsar=1,format=yuv420p",
             *codec_options,
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
            if movie.is_file() and movie.stat().st_size > MAX_ARCHIVE_BYTES:
                raise UgoiraExportError(f"转换文件超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB，请选择其他格式或原始帧 ZIP", "output_limit")
            if process.returncode != 0:
                raise UgoiraExportError("MP4 编码失败，请使用支持 H.264（libx264）的 FFmpeg，或选择 GIF／原始帧 ZIP", "encoder_failed")
            if movie.is_symlink() or not movie.is_file():
                raise UgoiraExportError("MP4 编码未生成有效文件", "encoder_failed")
            if not progress.is_file():
                raise UgoiraExportError("MP4 编码未返回完整进度，请重试或选择原始帧 ZIP", "encoder_failed")
            with progress.open("rb") as stream:
                stream.seek(max(0, progress.stat().st_size - 65536))
                values = dict(line.split("=", 1) for line in stream.read().decode("ascii", errors="replace").splitlines() if "=" in line)
            # -fs may stop early with exit zero: verify the original tail frame.
            if values.get("progress") != "end" or values.get("frame", "").strip() != str(frame_count + 1):
                raise UgoiraExportError("MP4 未完成全部帧，可能超过文件上限，请选择其他格式或原始帧 ZIP", "output_limit")
            with movie.open("rb") as stream:
                if stream.read(8)[4:8] != b"ftyp":
                    raise UgoiraExportError("MP4 编码未生成有效文件", "encoder_failed")
            return movie
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _mp4(raw, frames, encoder, output, check):
    from PIL import Image

    backend = os.environ.get("MOKU_UGOIRA_MP4_ENCODER", "cpu").strip().lower()
    if backend not in {"cpu", "nvenc"}:
        raise UgoiraExportError("MP4 编码器配置应为 cpu 或 nvenc")
    with tempfile.TemporaryDirectory(prefix="moku-encode-") as temporary:
        root = Path(temporary)
        lines = ["ffconcat version 1.0"]
        total_bytes = 0
        names = {}
        with closing(_frames(raw, frames, check)) as decoded:
            for index, (image, encoded, delay) in enumerate(decoded):
                frame_size = image.size
                remote_name = frames[index]["file"]
                name = names.get(remote_name)
                if name is None:
                    # Keep ordinary frames verbatim. Flatten transparency and
                    # strip non-default EXIF orientation only when necessary;
                    # raw pixel geometry must match the previous PNG pipeline.
                    normalize = (image.mode in {"RGBA", "LA"} or "transparency" in image.info
                                 or image.getexif().get(274, 1) != 1)
                    name = f"frame{index:06d}." + ("jpg" if image.format == "JPEG" and not normalize else "png")
                    needed = len(encoded) if not normalize else image.width * image.height * 4 + 65536
                    _check_frame_space(root, total_bytes, needed)
                    if normalize:
                        with closing(image.convert("RGBA")) as rgba:
                            with closing(Image.new("RGBA", image.size, "white")) as background:
                                background.alpha_composite(rgba)
                                rgb = background.convert("RGB")
                        with closing(rgb):
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
                with closing(Image.open(path)) as image:
                    _check_frame_space(root, total_bytes, image.width * image.height * 4 + 65536)
                    with closing(image.convert("RGB")) as rgb:
                        rgb.save(normalized, format="PNG", compress_level=1)
                total_bytes += normalized.stat().st_size - previous_size
                path.unlink()
                names[remote_name] = normalized.name
        for frame in frames:
            name = names[frame["file"]]
            lines.extend([f"file '{name}'", "option framerate 1000", f"duration {frame['delay'] / 1000:.3f}"])
        lines.extend([f"file '{name}'", "option framerate 1000"])
        (root / "frames.ffconcat").write_text("\n".join(lines) + "\n", encoding="ascii")
        threads = _mp4_cpu_threads(frame_size, len(frames))
        if backend == "nvenc":
            try:
                movie = _encode_mp4(root, encoder, len(frames), check, backend="nvenc", threads=threads)
            except UgoiraExportError as exc:
                if exc.code != "encoder_failed":
                    raise
                check()
                movie = _encode_mp4(root, encoder, len(frames), check, backend="cpu", threads=threads)
        else:
            movie = _encode_mp4(root, encoder, len(frames), check, backend="cpu", threads=threads)
        with movie.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                output.write(chunk)


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

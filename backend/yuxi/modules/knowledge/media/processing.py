"""在受限临时目录解码自有媒体，生成可追溯的完整片段与采样帧。"""

import asyncio
import io
import json
import math
import subprocess
import time
import wave
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from yuxi.infrastructure.filesystem import await_io
from yuxi.shared.errors import ContentTooLargeError, InvalidInputError, InvalidOperationError

MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_DERIVED_BYTES = 128 * 1024 * 1024
SEGMENT_MS = 12_000
FRAME_INTERVAL_MS = 2000


async def prepare_media(data: bytes, directory: Path) -> dict:
    """验证原始字节后执行同步解码；取消必须等待子进程收敛。"""
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ContentTooLargeError("媒体原件须在 100 MiB 以内", code="media_source_too_large")
    return await await_io(asyncio.to_thread(_prepare_media, data, directory))


def prepare_image(data: bytes) -> bytes:
    """实际图片解码到无元数据 PNG；不把扩展名或 OCR 当像素事实。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise InvalidOperationError("媒体图像只支持 PNG/JPEG/WebP", code="unsupported_media")
                if image.width * image.height > 25_000_000 or getattr(image, "n_frames", 1) != 1:
                    raise ContentTooLargeError("图像超过像素预算或包含未覆盖的动画帧", code="media_decode_budget")
                image.load()
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                encoded = output.getvalue()
                if len(encoded) > MAX_ASSET_BYTES:
                    raise ContentTooLargeError("解码图像超过 10 MiB", code="media_asset_too_large")
                return encoded
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise InvalidInputError("媒体图像内容无效", code="invalid_media") from error


def inspect_container(data: bytes) -> str:
    """只承接无外部清单语义的实际容器签名。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[4:8] == b"ftyp":
        return "mov"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "matroska"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (len(data) > 2 and data[0] == 255 and data[1] & 0xE0 == 0xE0):
        return "mp3"
    raise InvalidInputError("不支持或伪装的媒体容器；不读取播放列表", code="invalid_media_container")


def validate_probe(probe: dict) -> tuple[str, int, bool]:
    """按真实流参数限制解码总量；不能把超预算媒体静默截断。"""
    try:
        streams = probe["streams"]
        videos = [stream for stream in streams if stream["codec_type"] == "video"]
        audios = [stream for stream in streams if stream["codec_type"] == "audio"]
        if len(videos) > 1 or len(audios) > 1 or not videos and not audios:
            raise ValueError("streams")
        duration = float(probe["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0 or duration > 600:
            raise ContentTooLargeError("媒体时长须大于零且不超过 10 分钟", code="media_decode_budget")
        if audios and (int(audios[0]["channels"]) > 8 or int(audios[0]["sample_rate"]) > 192000):
            raise ContentTooLargeError("音频解码参数超过预算", code="media_decode_budget")
        if videos:
            video = videos[0]
            width, height = int(video["width"]), int(video["height"])
            numerator, denominator = map(float, video["avg_frame_rate"].split("/"))
            fps = numerator / denominator
            if (
                width <= 0
                or height <= 0
                or width * height > 4096 * 2160
                or not math.isfinite(fps)
                or fps <= 0
                or fps > 60
                or width * height * fps * duration > 4_000_000_000
            ):
                raise ContentTooLargeError("视频解码参数超过预算", code="media_decode_budget")
        return "video" if videos else "audio", math.ceil(duration * 1000), bool(audios)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise InvalidInputError("媒体流信息无效", code="invalid_media") from error


def _prepare_media(data: bytes, directory: Path) -> dict:
    """输出名完全由本进程生成；ffmpeg 不接受来自文件或模型的参数。"""
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF")) and not data[8:12] == b"WAVE":
        image = prepare_image(data)
        path = directory / "image.png"
        path.write_bytes(image)
        return {
            "modality": "image",
            "source_mime_type": "image/png"
            if data.startswith(b"\x89PNG")
            else "image/jpeg"
            if data.startswith(b"\xff\xd8")
            else "image/webp",
            "duration_ms": None,
            "coverage": "complete",
            "assets": [
                {
                    "asset_id": "image",
                    "segment_id": "image",
                    "kind": "image",
                    "mime_type": "image/png",
                    "path": path,
                }
            ],
        }
    deadline = time.monotonic() + 180
    format_name = inspect_container(data)
    source = directory / "source.bin"
    source.write_bytes(data)
    options = ["-protocol_whitelist", "file,pipe", "-format_whitelist", format_name, "-f", format_name]
    if format_name == "mov":
        options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    probe = json.loads(
        _run(
            ["ffprobe", "-v", "error", *options, "-show_format", "-show_streams", "-of", "json", str(source)], timeout=20, deadline=deadline
        )
    )
    modality, duration_ms, has_audio = validate_probe(probe)
    assets = []
    for start in range(0, duration_ms, SEGMENT_MS):
        end = min(start + SEGMENT_MS, duration_ms)
        segment_id = f"segment-{start:09d}-{end:09d}"
        base = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            *options,
            "-i",
            str(source),
            "-ss",
            str(start / 1000),
            "-t",
            str((end - start) / 1000),
        ]
        if modality == "video":
            path = directory / f"{segment_id}.mp4"
            _run(
                [
                    *base,
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(path),
                ],
                timeout=60,
                deadline=deadline,
            )
            assets.append(
                {
                    "asset_id": segment_id,
                    "segment_id": segment_id,
                    "kind": "video",
                    "mime_type": "video/mp4",
                    "path": path,
                    "start_ms": start,
                    "end_ms": end,
                }
            )
            require_derived_budget(assets)
        if has_audio:
            path = directory / f"{segment_id}.wav"
            _run([*base, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)], timeout=45, deadline=deadline)
            if modality == "video":
                # 音轨比画面短时，不把容器区间误报为已解码的音频证据。
                with wave.open(str(path), "rb") as decoded:
                    decoded_ms = 1000 * decoded.getnframes() / decoded.getframerate()
                if abs(decoded_ms - (end - start)) > 50:
                    raise InvalidInputError("视频音轨与画面时段不对齐", code="misaligned_video_audio")
            assets.append(
                {
                    "asset_id": f"{segment_id}-audio",
                    "segment_id": segment_id,
                    "kind": "audio",
                    "mime_type": "audio/wav",
                    "path": path,
                    "start_ms": start,
                    "end_ms": end,
                }
            )
            require_derived_budget(assets)
        if modality == "video":
            for timestamp in range(start, end, FRAME_INTERVAL_MS):
                path = directory / f"frame-{timestamp:09d}.png"
                _run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-nostdin",
                        "-y",
                        "-threads",
                        "1",
                        *options,
                        "-i",
                        str(source),
                        "-ss",
                        str(timestamp / 1000),
                        "-map",
                        "0:v:0",
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale='min(1280,iw)':-2",
                        str(path),
                    ],
                    timeout=30,
                    deadline=deadline,
                )
                assets.append(
                    {
                        "asset_id": path.stem,
                        "segment_id": segment_id,
                        "kind": "frame",
                        "mime_type": "image/png",
                        "path": path,
                        "start_ms": timestamp,
                        "end_ms": min(timestamp + FRAME_INTERVAL_MS, end),
                    }
                )
                require_derived_budget(assets)
    return {
        "modality": modality,
        "source_mime_type": {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "flac": "audio/flac",
            "ogg": f"{modality}/ogg",
            "mov": f"{modality}/mp4",
            "matroska": f"{modality}/x-matroska",
        }[format_name],
        "duration_ms": duration_ms,
        "coverage": "sampled_frames" if modality == "video" else "complete",
        "assets": assets,
        "frame_interval_ms": FRAME_INTERVAL_MS if modality == "video" else None,
    }


def require_derived_budget(assets: list[dict]) -> None:
    """每生成一个产物立即检查单对象和累计磁盘/后续内存预算。"""
    sizes = [asset["path"].stat().st_size if asset["path"].is_file() else 0 for asset in assets]
    if any(not 0 < size <= MAX_ASSET_BYTES for size in sizes):
        raise ContentTooLargeError("媒体派生产物为空或超过 10 MiB", code="media_asset_too_large")
    if sum(sizes) > MAX_DERIVED_BYTES:
        raise ContentTooLargeError("媒体派生产物累计超过 128 MiB", code="media_decode_budget")


def _run(argv: list[str], *, timeout: int, deadline: float) -> bytes:
    """禁止 shell，限制执行时间并只公开稳定的失败语义。"""
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContentTooLargeError("媒体解码超过总时间预算", code="media_decode_budget")
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=min(timeout, remaining), check=True)
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ContentTooLargeError("媒体探测响应超过预算", code="media_decode_budget")
        return result.stdout
    except FileNotFoundError as error:
        raise InvalidOperationError("原生媒体解码器未安装", code="media_decoder_unavailable") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise InvalidInputError("媒体解码失败或超时", code="media_decode_failed") from error

from __future__ import annotations

import io
import math
import warnings
import wave

from PIL import Image
from tinytag import TinyTag

from .core import AppError

MAX_MEDIA_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_ANIMATION_PIXELS = 32 * 1024 * 1024
MAX_FRAMES = 256
IMAGE_TYPES = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
    "GIF": ("image/gif", "gif"),
}
AUDIO_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac"}


def audio_kind(raw):
    if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
        return "wav"
    if raw.startswith(b"OggS") and (b"vorbis" in raw[:65536] or b"OpusHead" in raw[:65536]):
        return "ogg"
    if raw.startswith(b"fLaC"):
        return "flac"
    if raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 255 and raw[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


def probe_media(raw: bytes):
    if not raw or len(raw) > MAX_MEDIA_BYTES:
        raise AppError("media_too_large", "Файл пуст или превышает предел12МиБ.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=list(IMAGE_TYPES)) as image:
                mime, extension = IMAGE_TYPES[image.format]
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise AppError("media_decode_limit", "Изображение превышает предел декодирования.", 413)
                duration_ms, frames, pixels = 0, 0, 0
                # GIF.n_frames scans the entire file; inspect at most MAX_FRAMES+1.
                for index in range(MAX_FRAMES + 1):
                    try:
                        image.seek(index)
                    except EOFError:
                        break
                    pixels += image.width * image.height
                    if index >= MAX_FRAMES or pixels > MAX_ANIMATION_PIXELS:
                        raise AppError(
                            "media_decode_limit", "Анимация превышает предел кадров/декодирования.", 413
                        )
                    image.load()
                    frames += 1
                    duration_ms += max(0, min(600000, int(image.info.get("duration", 0))))
                return {
                    "type": mime,
                    "extension": extension,
                    "width": width,
                    "height": height,
                    "frames": frames,
                    "duration_seconds": duration_ms / 1000 if frames > 1 else None,
                    "size_bytes": len(raw),
                }
    except AppError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise AppError(
            "media_decode_limit", "Изображение превышает безопасный предел пикселей.", 413
        ) from exc
    except (OSError, ValueError, EOFError, SyntaxError):
        pass
    kind = audio_kind(raw)
    if kind is None:
        raise AppError(
            "unsupported_media", "Файл повреждён или формат не поддерживается. SVG/HTML не принимаются.", 422
        )
    try:
        audio = TinyTag.get(file_obj=io.BytesIO(raw), tags=False, image=False, check_magic_bytes=True)
        duration, rate, channels = audio.duration, audio.samplerate, audio.channels
        if (
            duration is None
            or not math.isfinite(duration)
            or not 0 < duration <= 4 * 3600
            or not rate
            or not 1 <= rate <= 384000
            or not channels
            or not 1 <= channels <= 8
        ):
            raise ValueError("audio limits")
        if kind == "wav":
            # Check the entire declared PCM frame range, not just its RIFF header.
            with wave.open(io.BytesIO(raw)) as wav:
                declared = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                if len(wav.readframes(wav.getnframes())) != declared:
                    raise ValueError("truncated WAV")
        return {
            "type": AUDIO_TYPES[kind],
            "extension": kind,
            "duration_seconds": duration,
            "sample_rate": rate,
            "channels": channels,
            "size_bytes": len(raw),
        }
    except Exception as exc:
        raise AppError(
            "invalid_media", "Аудиофайл повреждён, не поддерживается или превышает пределы.", 422
        ) from exc

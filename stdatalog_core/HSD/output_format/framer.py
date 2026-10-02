"""
Byte-stream framer for schema-backed acquisition payloads (DSF-style framing).

A frame is `samples_per_ts` fixed-size schema payloads, optionally followed by an 8-byte
little-endian double timestamp (existing DSF convention when timestamps are enabled). The framer
never receives timestamp-only bytes on its own and never emits a partial frame -- residual bytes
are retained across `feed()` calls.
"""

from __future__ import annotations

# Generic
import struct
from dataclasses import dataclass

_TIMESTAMP_BYTES = 8


@dataclass(frozen=True)
class DecodedFrame:
    payloads: tuple[bytes, ...]
    timestamp: float | None


class StreamFramer:
    def __init__(self, payload_size_bytes: int, *, samples_per_ts: int = 1, timestamp_enabled: bool = True) -> None:
        if payload_size_bytes < 1:
            raise ValueError('payload_size_bytes must be >= 1')
        if samples_per_ts < 1:
            raise ValueError('samples_per_ts must be >= 1')
        self._payload_size_bytes = payload_size_bytes
        self._samples_per_ts = samples_per_ts
        self._timestamp_enabled = timestamp_enabled
        self._frame_size = payload_size_bytes * samples_per_ts + (_TIMESTAMP_BYTES if timestamp_enabled else 0)
        self._buffer = bytearray()

    @property
    def frame_size_bytes(self) -> int:
        return self._frame_size

    @property
    def residual_byte_count(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[DecodedFrame]:
        self._buffer.extend(data)
        frames: list[DecodedFrame] = []
        while len(self._buffer) >= self._frame_size:
            frame_bytes = bytes(self._buffer[: self._frame_size])
            del self._buffer[: self._frame_size]
            frames.append(self._split_frame(frame_bytes))
        return frames

    def reset(self) -> None:
        self._buffer.clear()

    def _split_frame(self, frame_bytes: bytes) -> DecodedFrame:
        payloads = tuple(
            frame_bytes[index * self._payload_size_bytes : (index + 1) * self._payload_size_bytes]
            for index in range(self._samples_per_ts)
        )
        timestamp = struct.unpack('<d', frame_bytes[-_TIMESTAMP_BYTES:])[0] if self._timestamp_enabled else None
        return DecodedFrame(payloads=payloads, timestamp=timestamp)

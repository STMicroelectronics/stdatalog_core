"""
Decodes exactly one schema payload (see `compiler.py`) into typed, flattened leaf values.
"""

from __future__ import annotations

# Generic
import struct

# Owned
from stdatalog_core.HSD.output_format.compiler import CompiledOutputFormat, LeafDescriptor
from stdatalog_core.HSD.output_format.models import OutputFormatPrimitiveType

LeafValue = bool | int | float | list


class OutputFormatDecodeError(ValueError):
    """Raised when a payload does not match the compiled schema's expected size."""


class OutputFormatDecoder:
    """Decodes one fixed-size payload into a dict of dotted-path -> flattened leaf value."""

    def __init__(self, compiled: CompiledOutputFormat) -> None:
        self._compiled = compiled

    @property
    def payload_size_bytes(self) -> int:
        return self._compiled.payload_size_bytes

    @property
    def compiled(self) -> CompiledOutputFormat:
        return self._compiled

    def decode(self, payload: bytes) -> dict[str, LeafValue]:
        if len(payload) != self.payload_size_bytes:
            raise OutputFormatDecodeError(
                f'expected a {self.payload_size_bytes}-byte payload for schema '
                f'{self._compiled.name!r}, got {len(payload)} bytes'
            )
        raw = int.from_bytes(payload, byteorder='little', signed=False)
        return {leaf.path: _decode_leaf(raw, leaf) for leaf in self._compiled.leaves}


def _decode_leaf(raw: int, leaf: LeafDescriptor) -> LeafValue:
    if not leaf.shape:
        return _decode_scalar(raw, leaf.bit_offset, leaf.bit_width, leaf.type)
    if len(leaf.shape) == 1:
        return [
            _decode_scalar(raw, leaf.bit_offset + index * leaf.element_stride, leaf.bit_width, leaf.type)
            for index in range(leaf.shape[0])
        ]
    rows, columns = leaf.shape
    return [
        [
            _decode_scalar(
                raw,
                leaf.bit_offset + (row * columns + column) * leaf.element_stride,
                leaf.bit_width,
                leaf.type,
            )
            for column in range(columns)
        ]
        for row in range(rows)
    ]


def _decode_scalar(
    raw: int, bit_offset: int, bit_width: int, primitive_type: OutputFormatPrimitiveType
) -> bool | int | float:
    mask = (1 << bit_width) - 1
    bits = (raw >> bit_offset) & mask

    if primitive_type == OutputFormatPrimitiveType.BOOL:
        return bool(bits)
    if primitive_type == OutputFormatPrimitiveType.UINT:
        return bits
    if primitive_type == OutputFormatPrimitiveType.INT:
        if bits & (1 << (bit_width - 1)):
            bits -= 1 << bit_width
        return bits
    if primitive_type == OutputFormatPrimitiveType.FLOAT:
        return struct.unpack('<f', bits.to_bytes(4, 'little'))[0]
    if primitive_type == OutputFormatPrimitiveType.DOUBLE:
        return struct.unpack('<d', bits.to_bytes(8, 'little'))[0]
    raise OutputFormatDecodeError(f'unsupported primitive type: {primitive_type!r}')  # pragma: no cover

"""
Compiles a validated `OutputFormat` schema into a flat, ordered list of leaf descriptors.

Packing rules: declaration order, little-endian bytes, LSB-first packed bit fields, IEEE float
widths only (32/64 bits), two's-complement signed integers, final high-bit padding ignored (the
payload byte length is `ceil(total_bits / 8)`). Arrays/matrices are packed contiguously
(row-major for matrices); objects contribute no bits themselves, only their nested fields do.
"""

from __future__ import annotations

# Generic
from dataclasses import dataclass
from fractions import Fraction

# Owned
from stdatalog_core.HSD.output_format.models import (
    OutputFormat,
    OutputFormatArray,
    OutputFormatField,
    OutputFormatLayout,
    OutputFormatLayoutRow,
    OutputFormatMatrix,
    OutputFormatMatrixField,
    OutputFormatNode,
    OutputFormatObject,
    OutputFormatPrimitiveType,
    OutputFormatVisualization,
    OutputFormatVisualizationLayout,
)

MAX_SCHEMA_BYTES = 65536


class OutputFormatCompileError(ValueError):
    """Raised when a structurally valid schema cannot be compiled (bad refs, oversized, etc.)."""


@dataclass(frozen=True)
class ResolvedValidity:
    ref: str
    op: str
    value: float | int | bool


@dataclass(frozen=True)
class LeafDescriptor:
    """One decodable leaf: a dotted path plus its packing location and presentation metadata."""

    path: str
    type: OutputFormatPrimitiveType
    bit_offset: int
    bit_width: int
    element_stride: int
    shape: tuple[int, ...]
    unit: str | None
    visualization: tuple[OutputFormatVisualization, ...]
    visualization_layout: OutputFormatVisualizationLayout
    role: str | None
    validity: ResolvedValidity | None


@dataclass(frozen=True)
class CompiledOutputFormat:
    name: str
    schema_version: int
    total_bits: int
    leaves: tuple[LeafDescriptor, ...]
    layout: OutputFormatLayout | None = None

    @property
    def payload_size_bytes(self) -> int:
        return (self.total_bits + 7) // 8

    def leaf(self, path: str) -> LeafDescriptor | None:
        for candidate in self.leaves:
            if candidate.path == path:
                return candidate
        return None


class _BitCursor:
    def __init__(self) -> None:
        self.offset = 0

    def advance(self, bits: int) -> int:
        start = self.offset
        self.offset += bits
        return start


def compile_output_format(schema: OutputFormat, *, matrix_shape: tuple[int, int] | None = None) -> CompiledOutputFormat:
    """Compile a schema, optionally replacing every matrix shape for this stream instance."""
    if matrix_shape is not None and (
        len(matrix_shape) != 2 or any(not isinstance(size, int) or isinstance(size, bool) or size < 1 for size in matrix_shape)
    ):
        raise OutputFormatCompileError(f'invalid matrix shape: {matrix_shape!r}')
    leaves: list[LeafDescriptor] = []
    cursor = _BitCursor()
    for node in schema.outputs:
        _compile_node(node, prefix='', cursor=cursor, leaves=leaves, matrix_shape=matrix_shape)

    total_bits = cursor.offset
    if (total_bits + 7) // 8 > MAX_SCHEMA_BYTES:
        raise OutputFormatCompileError(
            f'schema {schema.name!r} exceeds the maximum payload size of {MAX_SCHEMA_BYTES} bytes'
        )

    leaf_paths = {leaf.path for leaf in leaves}
    for leaf in leaves:
        _check_validity_ref(leaf, leaf_paths, leaves)
    _check_no_validity_cycles(leaves)
    _check_layout(schema.layout, {leaf.path: {mode.value for mode in leaf.visualization} for leaf in leaves})

    return CompiledOutputFormat(
        name=schema.name,
        schema_version=schema.schema_version,
        total_bits=total_bits,
        leaves=tuple(leaves),
        layout=schema.layout,
    )


def _join_path(prefix: str, name: str) -> str:
    return f'{prefix}.{name}' if prefix else name


def _compile_node(
    node: OutputFormatNode,
    *,
    prefix: str,
    cursor: _BitCursor,
    leaves: list[LeafDescriptor],
    matrix_shape: tuple[int, int] | None,
) -> None:
    path = _join_path(prefix, node.name)

    if isinstance(node, OutputFormatObject):
        for child in node.fields:
            _compile_node(child, prefix=path, cursor=cursor, leaves=leaves, matrix_shape=matrix_shape)
        return

    if isinstance(node, OutputFormatField):
        shape: tuple[int, ...] = ()
        repeat = 1
    elif isinstance(node, OutputFormatArray):
        shape = (node.length,)
        repeat = node.length
    elif isinstance(node, OutputFormatMatrix):
        shape = matrix_shape or (node.rows, node.columns)
        repeat = shape[0] * shape[1]
        if node.fields is not None:
            cell_bits = sum(field.bits for field in node.fields)
            base_offset = cursor.advance(cell_bits * repeat)
            field_offset = 0
            for field in node.fields:
                leaves.append(
                    _leaf_descriptor(
                        field,
                        path=_join_path(path, field.name),
                        bit_offset=base_offset + field_offset,
                        shape=shape,
                        element_stride=cell_bits,
                    )
                )
                field_offset += field.bits
            return
    else:  # pragma: no cover - exhaustive per the discriminated union
        raise OutputFormatCompileError(f'unsupported node kind: {node!r}')

    bit_offset = cursor.advance(node.bits * repeat)
    leaves.append(_leaf_descriptor(node, path=path, bit_offset=bit_offset, shape=shape, element_stride=node.bits))


def _leaf_descriptor(
    node: OutputFormatField | OutputFormatArray | OutputFormatMatrixField,
    *,
    path: str,
    bit_offset: int,
    shape: tuple[int, ...],
    element_stride: int,
) -> LeafDescriptor:
    return LeafDescriptor(
        path=path,
        type=node.type,
        bit_offset=bit_offset,
        bit_width=node.bits,
        element_stride=element_stride,
        shape=shape,
        unit=node.unit,
        visualization=tuple(node.visualization),
        visualization_layout=node.visualization_layout,
        role=node.role,
        validity=(
            ResolvedValidity(ref=node.validity.ref, op=node.validity.op.value, value=node.validity.value)
            if node.validity
            else None
        ),
    )


def _check_validity_ref(leaf: LeafDescriptor, leaf_paths: set[str], all_leaves: list[LeafDescriptor]) -> None:
    if leaf.validity is None:
        return
    ref = leaf.validity.ref
    if ref == leaf.path:
        raise OutputFormatCompileError(f'validity rule on {leaf.path!r} cannot reference itself')
    if ref not in leaf_paths:
        raise OutputFormatCompileError(f'validity rule on {leaf.path!r} references unknown leaf {ref!r}')
    referenced = next(candidate for candidate in all_leaves if candidate.path == ref)
    if referenced.shape != leaf.shape:
        raise OutputFormatCompileError(f'validity rule on {leaf.path!r} references {ref!r} with a mismatched shape')


LAYOUT_VISUALIZATION_SEPARATOR = ':'


def _check_layout(layout: OutputFormatLayout | None, visualizations: dict[str, set[str]]) -> None:
    """Layout references are leaf paths, optionally suffixed `:<visualization>` to place just one plot."""
    if layout is None:
        return
    _check_layout_rows(layout.rows, visualizations, 'layout')
    for section in layout.sections:
        where = f'layout section {section.label!r}'
        _check_layout_rows(section.rows, visualizations, where)
        for path in section.paths:
            is_object = LAYOUT_VISUALIZATION_SEPARATOR not in path and any(
                leaf.startswith(f'{path}.') for leaf in visualizations
            )
            if not is_object:
                _check_layout_ref(path, visualizations, where)


def _check_layout_ref(ref: str, visualizations: dict[str, set[str]], where: str) -> None:
    path, separator, mode = ref.partition(LAYOUT_VISUALIZATION_SEPARATOR)
    if path not in visualizations:
        raise OutputFormatCompileError(f'{where} references unknown leaf {path!r}')
    if separator and mode not in visualizations[path] - {'hidden'}:
        raise OutputFormatCompileError(f'{where} references visualization {mode!r} not declared by {path!r}')


def _check_layout_rows(rows: list[OutputFormatLayoutRow], visualizations: dict[str, set[str]], where: str) -> None:
    # Rows may use different column counts, so cells are compared as fractions of the full width.
    cells: list[tuple[int, int, Fraction, Fraction]] = []
    for row in rows:
        track_count = max(column.column + column.span for column in row.columns)
        for column in row.columns:
            for ref in column.resolved_paths:
                _check_layout_ref(ref, visualizations, f'{where} row {row.row}')
            cells.append(
                (
                    row.row,
                    row.row + column.row_span,
                    Fraction(column.column, track_count),
                    Fraction(column.column + column.span, track_count),
                )
            )
    for index, (top, bottom, left, right) in enumerate(cells):
        for other_top, other_bottom, other_left, other_right in cells[index + 1 :]:
            if top < other_bottom and other_top < bottom and left < other_right and other_left < right:
                raise OutputFormatCompileError(f'{where} rows {top} and {other_top} contain overlapping cells')


def _check_no_validity_cycles(leaves: list[LeafDescriptor]) -> None:
    ref_by_path = {leaf.path: leaf.validity.ref for leaf in leaves if leaf.validity is not None}
    for start in ref_by_path:
        seen: set[str] = set()
        current = start
        while current in ref_by_path:
            if current in seen:
                raise OutputFormatCompileError(f'cyclic validity rule detected starting at {start!r}')
            seen.add(current)
            current = ref_by_path[current]

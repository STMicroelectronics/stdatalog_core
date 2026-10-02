"""
Descriptor models for named, versioned binary output-format schemas.

These models define a device-agnostic contract used to decode packed binary acquisition
payloads (see `compiler.py`/`decoder.py`) without hardcoding any sensor-specific parsing logic.
Every node in a schema is one of `field` (scalar), `array` (rank-1), `matrix` (rank-2), or
`object` (a non-leaf container of nested nodes) -- discriminated by the `kind` literal.
"""

from __future__ import annotations

# Generic
import re
from enum import StrEnum
from typing import Annotated, Literal

# Custom
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_NAME_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_SUPPORTED_SCHEMA_VERSIONS = (1,)
_MAX_BIT_WIDTH = 64
_FLOAT_BIT_WIDTH = 32
_DOUBLE_BIT_WIDTH = 64


def is_safe_output_format_name(name: str) -> bool:
    """Whether `name` is a safe schema/node identifier (used for both Pydantic and file names)."""
    return bool(_SAFE_NAME_PATTERN.match(name))


class OutputFormatPrimitiveType(StrEnum):
    UINT = 'uint'
    INT = 'int'
    BOOL = 'bool'
    FLOAT = 'float'
    DOUBLE = 'double'


_NUMERIC_PRIMITIVE_TYPES = (
    OutputFormatPrimitiveType.UINT,
    OutputFormatPrimitiveType.INT,
    OutputFormatPrimitiveType.FLOAT,
    OutputFormatPrimitiveType.DOUBLE,
)


class OutputFormatVisualization(StrEnum):
    LINE = 'line'
    VALUE = 'value'
    HEATMAP = 'heatmap'
    HISTOGRAM = 'histogram'
    HIDDEN = 'hidden'


class OutputFormatVisualizationLayout(StrEnum):
    VERTICAL = 'vertical'
    HORIZONTAL = 'horizontal'


_COMPATIBLE_VISUALIZATION_PAIRS = frozenset(
    {frozenset({OutputFormatVisualization.HEATMAP, OutputFormatVisualization.HISTOGRAM})}
)


class ValidityComparisonOp(StrEnum):
    EQ = 'eq'
    NE = 'ne'
    LT = 'lt'
    LTE = 'lte'
    GT = 'gt'
    GTE = 'gte'


class OutputFormatValidityRule(BaseModel):
    """Declarative rule masking a leaf's value based on another same-shape leaf's value."""

    model_config = ConfigDict(extra='forbid')

    ref: str = Field(..., description='Dotted path of the leaf whose value this rule compares against.')
    op: ValidityComparisonOp = Field(..., description='Comparison operator applied to the referenced leaf.')
    value: float | int | bool = Field(..., description='Operand compared against the referenced leaf value.')


def _validate_bits(primitive_type: OutputFormatPrimitiveType, bits: int) -> None:
    if primitive_type == OutputFormatPrimitiveType.BOOL:
        if bits != 1:
            raise ValueError('bool leaves must declare bits=1')
    elif primitive_type == OutputFormatPrimitiveType.FLOAT:
        if bits != _FLOAT_BIT_WIDTH:
            raise ValueError(f'float leaves must declare bits={_FLOAT_BIT_WIDTH}')
    elif primitive_type == OutputFormatPrimitiveType.DOUBLE:
        if bits != _DOUBLE_BIT_WIDTH:
            raise ValueError(f'double leaves must declare bits={_DOUBLE_BIT_WIDTH}')
    elif not 1 <= bits <= _MAX_BIT_WIDTH:
        raise ValueError(f'bit width must be between 1 and {_MAX_BIT_WIDTH}, got {bits}')


def _validate_visualization(modes: list[OutputFormatVisualization]) -> list[OutputFormatVisualization]:
    if not modes:
        raise ValueError('visualization must declare at least one mode')
    if len(modes) > 2:
        raise ValueError('visualization may declare at most a primary mode and one companion mode')
    if OutputFormatVisualization.HIDDEN in modes and len(modes) > 1:
        raise ValueError('hidden cannot be combined with another visualization mode')
    if len(modes) == 2 and frozenset(modes) not in _COMPATIBLE_VISUALIZATION_PAIRS:
        raise ValueError(f'incompatible visualization mode pair: {sorted(m.value for m in modes)}')
    return modes


def _validate_leaf_visualization_shape(
    modes: list[OutputFormatVisualization],
    *,
    primitive_type: OutputFormatPrimitiveType,
    shape: tuple[int, ...],
) -> None:
    is_numeric = primitive_type in _NUMERIC_PRIMITIVE_TYPES
    is_bool = primitive_type == OutputFormatPrimitiveType.BOOL
    for mode in modes:
        if mode == OutputFormatVisualization.LINE:
            if shape != () or not is_numeric:
                raise ValueError('line visualization requires a numeric scalar leaf')
        elif mode == OutputFormatVisualization.HEATMAP:
            if len(shape) != 2 or not (is_numeric or is_bool):
                raise ValueError('heatmap visualization requires a rank-2 numeric or bool leaf')
        elif mode == OutputFormatVisualization.HISTOGRAM:
            if len(shape) not in (1, 2) or not is_numeric:
                raise ValueError('histogram visualization requires a numeric rank-1 or rank-2 leaf')
        elif mode == OutputFormatVisualization.VALUE:
            if len(shape) > 1:
                raise ValueError('value visualization requires a scalar or rank-1 leaf')
        # hidden: any leaf shape/type is valid, nothing to check.


def _check_unique_sibling_names(nodes: list[OutputFormatNode]) -> list[OutputFormatNode]:
    seen: set[str] = set()
    for node in nodes:
        if node.name in seen:
            raise ValueError(f'duplicate sibling name: {node.name!r}')
        seen.add(node.name)
    return nodes


class _LeafMetadataMixin(BaseModel):
    """Shared presentation/semantic metadata for `field`/`array`/`matrix` leaves."""

    model_config = ConfigDict(extra='forbid')

    unit: str | None = Field(default=None, description='Physical unit of the leaf value(s), if any.')
    visualization: list[OutputFormatVisualization] = Field(
        default_factory=lambda: [OutputFormatVisualization.HIDDEN],
        description='Presentation mode(s) for this leaf (one primary, one optional compatible companion).',
    )
    visualization_layout: OutputFormatVisualizationLayout = Field(
        default=OutputFormatVisualizationLayout.VERTICAL,
        description='Arrangement of multiple visualizations declared by this leaf.',
    )
    role: str | None = Field(default=None, description='Stable semantic role, e.g. "distance"/"validity"/"amplitude".')
    validity: OutputFormatValidityRule | None = Field(
        default=None, description='Declarative validity rule referencing another same-shape leaf.'
    )

    @field_validator('visualization')
    @classmethod
    def _check_visualization(cls, value: list[OutputFormatVisualization]) -> list[OutputFormatVisualization]:
        return _validate_visualization(value)


class OutputFormatField(_LeafMetadataMixin):
    """A single scalar leaf value."""

    kind: Literal['field'] = 'field'
    name: str
    type: OutputFormatPrimitiveType
    bits: int

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid field name: {value!r}')
        return value

    @model_validator(mode='after')
    def _check_bits_and_shape(self) -> OutputFormatField:
        _validate_bits(self.type, self.bits)
        _validate_leaf_visualization_shape(self.visualization, primitive_type=self.type, shape=())
        return self


class OutputFormatArray(_LeafMetadataMixin):
    """A fixed-length rank-1 leaf of homogeneous scalars."""

    kind: Literal['array'] = 'array'
    name: str
    type: OutputFormatPrimitiveType
    bits: int
    length: int

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid array name: {value!r}')
        return value

    @field_validator('length')
    @classmethod
    def _check_length(cls, value: int) -> int:
        if value < 1:
            raise ValueError('array length must be >= 1')
        return value

    @model_validator(mode='after')
    def _check_bits_and_shape(self) -> OutputFormatArray:
        _validate_bits(self.type, self.bits)
        _validate_leaf_visualization_shape(self.visualization, primitive_type=self.type, shape=(self.length,))
        return self


class OutputFormatMatrixField(_LeafMetadataMixin):
    """One packed scalar subfield repeated in every cell of a composite matrix."""

    name: str
    type: OutputFormatPrimitiveType
    bits: int

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid matrix field name: {value!r}')
        return value


class OutputFormatMatrix(_LeafMetadataMixin):
    """A fixed rows x columns matrix of homogeneous or packed per-cell scalar values."""

    kind: Literal['matrix'] = 'matrix'
    name: str
    type: OutputFormatPrimitiveType | None = None
    bits: int | None = None
    rows: int
    columns: int
    fields: list[OutputFormatMatrixField] | None = None

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid matrix name: {value!r}')
        return value

    @field_validator('rows', 'columns')
    @classmethod
    def _check_dim(cls, value: int) -> int:
        if value < 1:
            raise ValueError('matrix dimensions must be >= 1')
        return value

    @model_validator(mode='after')
    def _check_bits_and_shape(self) -> OutputFormatMatrix:
        shape = (self.rows, self.columns)
        if self.fields is not None:
            if not self.fields:
                raise ValueError('composite matrices must declare at least one field')
            if self.type is not None or self.bits is not None:
                raise ValueError('composite matrices cannot declare type or bits')
            _check_unique_sibling_names(self.fields)
            for field in self.fields:
                _validate_bits(field.type, field.bits)
                _validate_leaf_visualization_shape(field.visualization, primitive_type=field.type, shape=shape)
        else:
            if self.type is None or self.bits is None:
                raise ValueError('homogeneous matrices must declare type and bits')
            _validate_bits(self.type, self.bits)
            _validate_leaf_visualization_shape(self.visualization, primitive_type=self.type, shape=shape)
        return self


class OutputFormatObject(BaseModel):
    """A non-leaf container grouping nested nodes under one dotted-path prefix."""

    model_config = ConfigDict(extra='forbid')

    kind: Literal['object'] = 'object'
    name: str
    fields: list[OutputFormatNode] = Field(..., min_length=1)

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid object name: {value!r}')
        return value

    @field_validator('fields')
    @classmethod
    def _check_unique_siblings(cls, value: list[OutputFormatNode]) -> list[OutputFormatNode]:
        return _check_unique_sibling_names(value)


class OutputFormatLayoutColumn(BaseModel):
    """Placement of one layout cell; a cell with several paths shows one selectable leaf at a time."""

    model_config = ConfigDict(extra='forbid')

    column: int = Field(..., ge=0)
    path: str | None = None
    paths: list[str] | None = None
    span: int = Field(default=1, ge=1)
    row_span: int = Field(default=1, ge=1)

    @model_validator(mode='after')
    def _check_paths(self) -> OutputFormatLayoutColumn:
        if (self.path is None) == (self.paths is None):
            raise ValueError('layout cells must declare exactly one of path or paths')
        if self.paths is not None:
            if not self.paths:
                raise ValueError('layout cell paths must not be empty')
            if len(self.paths) != len(set(self.paths)):
                raise ValueError('layout cell paths must be unique')
        return self

    @property
    def resolved_paths(self) -> list[str]:
        return self.paths if self.paths is not None else [self.path or '']


class OutputFormatLayoutRow(BaseModel):
    """One explicitly positioned row; rows may contain different column counts."""

    model_config = ConfigDict(extra='forbid')

    row: int = Field(..., ge=0)
    columns: list[OutputFormatLayoutColumn] = Field(..., min_length=1)

    @field_validator('columns')
    @classmethod
    def _check_unique_columns(cls, value: list[OutputFormatLayoutColumn]) -> list[OutputFormatLayoutColumn]:
        positions = [column.column for column in value]
        if len(positions) != len(set(positions)):
            raise ValueError('layout columns must be unique within a row')
        return value


def _check_unique_row_positions(value: list[OutputFormatLayoutRow]) -> list[OutputFormatLayoutRow]:
    positions = [row.row for row in value]
    if len(positions) != len(set(positions)):
        raise ValueError('layout rows must be unique')
    return value


class OutputFormatLayoutSection(BaseModel):
    """Collapsible group of outputs hidden until the user expands it."""

    model_config = ConfigDict(extra='forbid')

    label: str = Field(..., min_length=1, description='Toggle label, e.g. "Advanced plots".')
    rows: list[OutputFormatLayoutRow] = Field(default_factory=list, description='Positioned grid inside the section.')
    paths: list[str] = Field(
        default_factory=list,
        description='Leaf or object paths flowed in an automatic grid after the section rows.',
    )
    row_height: int | None = Field(default=None, ge=40, le=2000, description='Grid row height in px.')
    expanded: bool = Field(default=False, description='Initial expansion state.')

    @field_validator('paths')
    @classmethod
    def _check_unique_paths(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('layout section paths must be unique')
        return value

    @field_validator('rows')
    @classmethod
    def _check_unique_rows(cls, value: list[OutputFormatLayoutRow]) -> list[OutputFormatLayoutRow]:
        return _check_unique_row_positions(value)

    @model_validator(mode='after')
    def _check_not_empty(self) -> OutputFormatLayoutSection:
        if not self.rows and not self.paths:
            raise ValueError('layout sections must declare rows or paths')
        return self


class OutputFormatLayout(BaseModel):
    """Optional plot arrangement for a structured output format."""

    model_config = ConfigDict(extra='forbid')

    rows: list[OutputFormatLayoutRow] = Field(default_factory=list)
    row_height: int | None = Field(default=None, ge=40, le=2000, description='Grid row height in px.')
    sections: list[OutputFormatLayoutSection] = Field(default_factory=list)

    @field_validator('rows')
    @classmethod
    def _check_unique_rows(cls, value: list[OutputFormatLayoutRow]) -> list[OutputFormatLayoutRow]:
        return _check_unique_row_positions(value)

    @model_validator(mode='after')
    def _check_not_empty(self) -> OutputFormatLayout:
        if not self.rows and not self.sections:
            raise ValueError('layout must declare at least one row or section')
        return self


OutputFormatNode = Annotated[
    OutputFormatField | OutputFormatArray | OutputFormatMatrix | OutputFormatObject,
    Field(discriminator='kind'),
]

OutputFormatObject.model_rebuild()


class OutputFormat(BaseModel):
    """Root descriptor for a named, versioned binary output-format schema."""

    model_config = ConfigDict(extra='forbid')

    schema_version: int = Field(default=1, description='Schema contract version.')
    name: str = Field(..., description='Stable, unique schema identifier.')
    description: str | None = Field(default=None, description='Human-readable description.')
    outputs: list[OutputFormatNode] = Field(..., min_length=1, description='Ordered top-level output leaves.')
    layout: OutputFormatLayout | None = Field(default=None, description='Optional structured-plot layout.')

    @field_validator('name')
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not is_safe_output_format_name(value):
            raise ValueError(f'invalid schema name: {value!r}')
        return value

    @field_validator('schema_version')
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f'unsupported schema_version: {value}')
        return value

    @field_validator('outputs')
    @classmethod
    def _check_unique_outputs(cls, value: list[OutputFormatNode]) -> list[OutputFormatNode]:
        return _check_unique_sibling_names(value)

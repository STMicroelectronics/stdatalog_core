"""Versioned binary output formats shared by SDK consumers."""

import json
from functools import lru_cache
from pathlib import Path

from .compiler import CompiledOutputFormat, OutputFormatCompileError, compile_output_format
from .decoder import OutputFormatDecodeError, OutputFormatDecoder
from .framer import StreamFramer
from .models import OutputFormat, is_safe_output_format_name

BUNDLED_SCHEMAS_DIR = Path(__file__).parent / 'schemas'
VL53L9_RAW_DATA_DISCLAIMER = (
    "Unprocessed sensor data is shown here. Use ST's official IPP library to "
    "process it before use."
)


@lru_cache(maxsize=32)
def load_schema(name: str) -> OutputFormat:
    """Load a bundled descriptor by its safe registered name."""
    if not is_safe_output_format_name(name):
        raise ValueError(f'invalid output format name: {name!r}')
    path = BUNDLED_SCHEMAS_DIR / f'{name}.json'
    if not path.is_file():
        raise ValueError(f'unknown output format: {name!r}')
    schema = OutputFormat.model_validate(json.loads(path.read_text(encoding='utf-8')))
    if schema.name != name:
        raise ValueError(f'output format name mismatch: {name!r}')
    return schema


@lru_cache(maxsize=64)
def get_compiled(name: str, matrix_shape: tuple[int, int] | None = None) -> CompiledOutputFormat:
    return compile_output_format(load_schema(name), matrix_shape=matrix_shape)


def column_names(compiled: CompiledOutputFormat) -> list[str]:
    """Flat declaration-order leaf names for one sample."""
    names = []
    for leaf in compiled.leaves:
        if not leaf.shape:
            names.append(leaf.path)
        elif len(leaf.shape) == 1:
            names.extend(f'{leaf.path}[{index}]' for index in range(leaf.shape[0]))
        else:
            rows, columns = leaf.shape
            names.extend(
                f'{leaf.path}[{row},{column}]'
                for row in range(rows)
                for column in range(columns)
            )
    return names
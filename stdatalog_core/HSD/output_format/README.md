# Output format contract

`OutputFormat` is the versioned, device-independent schema for binary sensor
samples. Use `load_schema(name)` to read a bundled schema, `get_compiled(name,
(rows, columns))` to resolve a stream's matrix shape, and
`OutputFormatDecoder(compiled).decode(payload)` to decode exactly one sample.
The core codec depends only on Pydantic; it does not import Qt, pandas, or the
Dataset Factory backend.

## Authoring

Store a `schema_version: 1` JSON descriptor in `schemas/` with a safe identifier
as its filename and `name`. Declare nodes in wire order. Nodes may be scalar
`field`, one-dimensional `array`, two-dimensional `matrix`, or nested `object`.
Composite matrices declare their per-cell `fields` in bit order. Each leaf may
declare a `role`, `unit`, `visualization` (`heatmap`, `histogram`, `line`, `value`,
or `hidden`), and a `validity` rule referencing another leaf of the same shape.
Only declared validity rules mask values. Layout rows, spans, sections, and
references must refer to declared visualizations.

Fields are packed LSB-first within little-endian bytes with no implicit
alignment between leaves. Matrices are row-major; signed integers are two's
complement, and float/double use 32/64-bit IEEE little-endian values. The
payload length is `ceil(total_bits / 8)`; trailing high padding bits are ignored,
but truncated and oversized payloads are rejected. Compiled samples are limited
to 65,536 bytes. A timestamp is **not** part of a sample: for timestamped DSF
frames, `samples_per_ts` payloads are followed by one 8-byte little-endian
double timestamp. `StreamFramer` retains incomplete frames across calls.

Resolution labels mean `columns x rows`; pass `(rows, columns)` when compiling.
Validate the compiled payload byte count against `dim * data_type_size` before
decoding. The VL53L9 DTDL enum labels are `4x4`, `8x8`, `12x10`, `18x14`,
`24x24`, and `54x42`; its recorded 54x42 capture has 14,842 bytes per sample.
Do not infer matrix dimensions from byte count alone.

Offline tables name leaves in declaration order: matrix `path[row,col]`, array
`path[index]`, scalar `path`, with `Time` first and tag columns last. Booleans,
signed/unsigned integers and floats retain their decoded types. For schema
payloads, `raw_flag` leaves decoded values unscaled; physical scaling requires
an explicit schema rule. Dict and absent `output_format` values use the existing
legacy extraction/plot paths. Dataset Factory may override bundled schemas in
its app-data registry, without changing the core bit codec.

Run `python -m unittest discover -s tests -p test_output_format.py` from the SDK
root for the focused contract and recorded-capture checks. Dataset Factory uses
an editable SDK-core dependency in development and pins version `0.1.0` in its
backend manifest; publish that version before making a distributable build.
"""Binary and numeric type conversion utilities for STDatalog.

Minimal module docstring to satisfy linters; functions documented inline.
"""

# ******************************************************************************
# * @attention
# *
# * Copyright (c) 2022 STMicroelectronics.
# * All rights reserved.
# *
# * This software is licensed under terms that can be found in the LICENSE file
# * in the root directory of this software component.
# * If no LICENSE file comes with this software, it is provided AS-IS.
# *
# *
# ******************************************************************************
#
# Module purpose:
#   - Provide utilities to convert between firmware/SDK data type strings and
#     Python struct format characters, human-readable formats, NumPy dtypes, and
#     raw byte buffers.
# Design notes:
#   - Centralize type conversion logic to avoid duplication and errors.
#   - Handle special cases like 24-bit integers with custom logic.

import numpy as np


class TypeConversion:
    """
    Utility class collecting helpers to convert between:
        - SDK/firmware data type strings (e.g., 'int16_t', 'float', 'double_t', 'enum')
        - Python struct format characters (for binary unpacking)
        - Human-readable formatting strings
        - NumPy dtypes (for efficient numeric arrays)
        - Raw byte buffers and typed arrays (e.g., special handling for 24-bit integers)

    Rationale:
        - Devices and firmware often use C-style type names that do not map directly to
            Python/NumPy.
        - Centralizing these mappings reduces duplication and errors, and simplifies
            decoding pipelines.
    """

    @staticmethod
    def check_type(check_type):
        """
        Normalize a firmware/SDK type string to a canonical name.

        Input:
            check_type: str
                A type string coming from firmware or metadata,
                e.g., 'int16_t', 'float_t', 'double'.

        Returns:
            str:
                Canonical type name used within this SDK (e.g., 'int16', 'float32', 'double').
                Returns "error" for unknown types.

        Notes:
            - Some aliases map to the same canonical name (e.g., 'float', 'float_t' -> 'float32').
            - 'enum' is represented as 'uint8' (typical underlying storage).
        """
        switcher = {
            "uint8_t": "uint8",
            "uint8": "uint8",
            "int8_t": "int8",
            "int8": "int8",
            "uint16_t": "uint16",
            "uint16": "uint16",
            "int16_t": "int16",
            "int16": "int16",
            "uint24_t": "uint24",
            "uint24": "uint24",
            "int24_t": "int24",
            "int24": "int24",
            "uint32_t": "uint32",
            "uint32": "uint32",
            "int32_t": "int32",
            "int32": "int32",
            "uint64_t": "uint64",
            "uint64": "uint64",
            "int64_t": "int64",
            "int64": "int64",
            "float_t": "float32",
            "float": "float32",
            "double_t": "double",
            "double": "double",
            "enum": "uint8",
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def check_type_length(check_type):
        """
        Get the size in bytes for one value of the given type.

        Input:
            check_type: str
                Canonical or firmware type string.

        Returns:
            int or "error":
                Number of bytes for a single value (e.g., 2 for int16, 4 for float32).
                Returns "error" for unknown types.

        Special cases:
            - 'int24'/'int24_t' uses 3 bytes per value (packed 24-bit signed integer).
            - 'enum' uses 1 byte (uint8) by convention in this SDK.
        """
        switcher = {
            "uint8_t": 1,
            "uint8": 1,
            "int8_t": 1,
            "int8": 1,
            "uint16_t": 2,
            "uint16": 2,
            "int16_t": 2,
            "int16": 2,
            "uint24_t": 3,
            "uint24": 3,
            "int24_t": 3,
            "int24": 3,
            "uint32_t": 4,
            "uint32": 4,
            "int32_t": 4,
            "int32": 4,
            "uint64_t": 8,
            "uint64": 8,
            "int64_t": 8,
            "int64": 8,
            "float_t": 4,
            "float": 4,
            "float32": 4,
            "double_t": 8,
            "double": 8,
            "enum": 1,
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def get_format_char(check_type):
        """
        Map type to Python 'struct' format character for binary unpacking.

        Input:
            check_type: str
                Canonical or firmware type string.

        Returns:
            str or "error":
                Struct format character (e.g., 'h' for int16, 'f' for float32).
                Returns "error" for unknown types.

        Notes:
            - The returned char is only the type code; you must
                prepend byte order ('=', '<', '>') yourself.
            - 'int24'/'int24_t' is mapped to 'i' (int32) because Python's struct
                does not support 24-bit.
                Use int24_buffer_to_int32_buffer for proper 24-bit handling with sign extension.
        """
        switcher = {
            "uint8_t": "B",
            "uint8": "B",
            "int8_t": "b",
            "int8": "b",
            "uint16_t": "H",
            "uint16": "H",
            "int16_t": "h",
            "int16": "h",
            "uint24_t": "I",  # packed as int32
            "uint24": "I",    # packed as int32
            "int24_t": "i",   # packed as int32
            "int24": "i",     # packed as int32
            "uint32_t": "I",
            "uint32": "I",
            "int32_t": "i",
            "int32": "i",
            "uint64_t": "Q",
            "uint64": "Q",
            "int64_t": "q",
            "int64": "q",
            "float_t": "f",
            "float": "f",
            "float32": "f",
            "double_t": "d",
            "double": "d",
            "enum": "B",
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def get_str_format(check_type):
        """
        Provide a human-readable formatting string for values of the given type.

        Input:
            check_type: str

        Returns:
            str or "error":
                Format string compatible with str.format, e.g., '{:.0f}' for integers,
                '{:.6f}' for floats/doubles. Returns "error" for unknown types.

        Usage:
            fmt = TypeConversion.get_str_format('float32')
            s = fmt.format(3.1415926)  # -> '3.141593'
        """
        switcher = {
            "uint8_t": "{:.0f}",
            "uint8": "{:.0f}",
            "int8_t": "{:.0f}",
            "int8": "{:.0f}",
            "uint16_t": "{:.0f}",
            "uint16": "{:.0f}",
            "int16_t": "{:.0f}",
            "int16": "{:.0f}",
            "uint24_t": "{:.0f}",
            "uint24": "{:.0f}",
            "int24_t": "{:.0f}",
            "int24": "{:.0f}",
            "uint32_t": "{:.0f}",
            "uint32": "{:.0f}",
            "int32_t": "{:.0f}",
            "int32": "{:.0f}",
            "uint64_t": "{:.0f}",
            "uint64": "{:.0f}",
            "int64_t": "{:.0f}",
            "int64": "{:.0f}",
            "float_t": "{:.6f}",
            "float": "{:.6f}",
            "float32": "{:.6f}",
            "double_t": "{:.6f}",
            "double": "{:.6f}",
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def get_np_dtype(check_type):
        """
        Map type to NumPy dtype for efficient array creation and computations.

        Input:
            check_type: str

        Returns:
            numpy.dtype or "error":
                Appropriate NumPy dtype (e.g., np.int16, np.float32).
                Returns "error" for unknown types.

        Notes:
            - 'int24'/'int24_t' maps to np.int32 because NumPy does not support 24-bit storage.
                Use int24_buffer_to_int32_buffer to expand raw 24-bit bytes
                into 32-bit integers with sign extension.
            - 'double' maps to np.double (alias of np.float64).
        """
        switcher = {
            "uint8_t": np.uint8,
            "uint8": np.uint8,
            "int8_t": np.int8,
            "int8": np.int8,
            "uint16_t": np.uint16,
            "uint16": np.uint16,
            "int16_t": np.int16,
            "int16": np.int16,
            "uint24_t": np.uint32,  # expanded to 32-bit
            "uint24": np.uint32,    # expanded to 32-bit
            "int24_t": np.int32,    # expanded to 32-bit
            "int24": np.int32,      # expanded to 32-bit
            "uint32_t": np.uint32,
            "uint32": np.uint32,
            "int32_t": np.int32,
            "int32": np.int32,
            "uint64_t": np.uint64,
            "uint64": np.uint64,
            "int64_t": np.int64,
            "int64": np.int64,
            "float_t": np.float32,
            "float": np.float32,
            "float32": np.float32,
            "double_t": np.double,  # np.float64
            "double": np.double,    # np.float64
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def int24_buffer_to_int32_buffer(buffer):
        """
        Convert a raw bytes buffer of packed 24-bit signed integers into a bytes buffer
            of 32-bit signed integers.

        Input:
            buffer: bytes or bytearray
                Raw data where each integer is stored on 3 bytes
                (24-bit two's complement), little-endian.

        Returns:
            bytes:
                A new bytes object representing the same numeric sequence expanded
                to 32-bit signed integers.
                Useful to pass into NumPy with dtype=np.int32 or struct with 'i' code.

        Raises:
            ValueError:
                If buffer length is not a multiple of 3 (not aligned to 24-bit integers).

        Implementation details:
            - The incoming bytes are interpreted as little-endian:
                byte0 = least significant byte (LSB)
                byte1 = middle byte
                byte2 = most significant byte (MSB)
            - We reconstruct each 24-bit integer into a 32-bit container:
                int32 = (MSB << 16) | (MID << 8) | LSB
            - Sign extension:
                If bit 23 (0x800000) is set, the value is negative in 24-bit two's complement.
                We extend sign into 32-bit by OR-ing with 0xFF000000, producing the
                correct negative value.
            - The final result is returned as bytes to allow flexible downstream consumption.
        """
        # Ensure the buffer length is a multiple of 3 bytes (24 bits)
        if len(buffer) % 3 != 0:
            raise ValueError("Buffer length must be a multiple of 3 bytes")

        # Convert the buffer to a NumPy array of uint8 for vectorized operations
        byte_array = np.frombuffer(buffer, dtype=np.uint8)

        # Reshape the byte array to have 3 columns:
        # each row corresponds to one 24-bit integer (LSB, MID, MSB)
        byte_array = byte_array.reshape(-1, 3)

        # Recompose 24-bit little-endian values into 32-bit containers
        # Cast to uint32 to avoid overflow when shifting/OR-ing
        int32_array = (
            (byte_array[:, 2].astype(np.uint32) << 16)
            | (byte_array[:, 1].astype(np.uint32) << 8)
            | byte_array[:, 0].astype(np.uint32)
        )

        # Sign extension for 24-bit two's complement:
        # If bit 23 is set (0x800000), propagate the sign into the upper 8 bits of 32-bit.
        int32_array = np.where(int32_array & 0x800000, int32_array | 0xFF000000, int32_array)

        # Return as raw bytes (caller can wrap with np.frombuffer(..., dtype=np.int32) if needed)
        return int32_array.tobytes()

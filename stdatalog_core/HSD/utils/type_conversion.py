
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

import numpy as np
class TypeConversion:

    @staticmethod
    def check_type(check_type):
        switcher = {
            'uint8_t': 'uint8',
            'uint8': 'uint8',
            'uint16_t': 'uint16',
            'uint16': 'uint16',
            'uint32_t': 'uint32',
            'uint32': 'uint32',
            'int8_t': 'int8',
            'int8': 'int8',
            'int16_t': 'int16',
            'int16': 'int16',
            'int24_t': 'int24',
            'int24': 'int24',
            'int32_t': 'int32',
            'int32': 'int32',
            'float_t': 'float32',
            'float': 'float32',
            'double_t': 'double',
            'double': 'double',
            'enum': 'uint8'
        }
        return switcher.get(check_type, "error")

    @staticmethod
    def check_type_length(check_type):
        switcher = {
            'uint8_t': 1,
            'uint8': 1,
            'int8_t': 1,
            'int8': 1,
            'uint16_t': 2,
            'uint16': 2,
            'int16_t': 2,
            'int16': 2,
            'int24_t': 3,
            'int24': 3,
            'uint32_t': 4,
            'uint32': 4,
            'int32_t': 4,
            'int32': 4,
            'float_t': 4,
            'float': 4,
            'float32': 4,
            'double_t': 8,
            'double': 8,
            'enum':1
        }
        return switcher.get(check_type, "error")
    
    @staticmethod
    def get_format_char(check_type):
        switcher = {
            'uint8_t': 'B',
            'uint8': 'B',
            'int8_t': 'b',
            'int8': 'b',
            'uint16_t': 'H',
            'uint16': 'H',
            'int16_t': 'h',
            'int16': 'h',
            'int24_t': 'i',
            'int24': 'i',
            'uint32_t': 'I',
            'uint32': 'I',
            'int32_t': 'i',
            'int32': 'i',
            'float_t': 'f',
            'float': 'f',
            'float32': 'f',
            'double_t': 'd',
            'double': 'd',
            'enum': 'B'
        }
        return switcher.get(check_type, "error")
    
    @staticmethod
    def get_str_format(check_type):
        switcher = {
            'uint8_t': '{:.0f}',
            'uint8': '{:.0f}',
            'int8_t': '{:.0f}',
            'int8': '{:.0f}',
            'uint16_t': '{:.0f}',
            'uint16': '{:.0f}',
            'int16_t': '{:.0f}',
            'int16': '{:.0f}',
            'int24_t': '{:.0f}',
            'int24': '{:.0f}',
            'uint32_t': '{:.0f}',
            'uint32': '{:.0f}',
            'int32_t': '{:.0f}',
            'int32': '{:.0f}',
            'float_t': '{:.6f}',
            'float': '{:.6f}',
            'float32': '{:.6f}',
            'double_t': '{:.6f}',
            'double': '{:.6f}',
        }
        return switcher.get(check_type, "error")
    
    @staticmethod
    def get_np_dtype(check_type):
        switcher = {
            'uint8_t': np.uint8,
            'uint8': np.uint8,
            'int8_t': np.int8,
            'int8': np.int8,
            'uint16_t': np.uint16,
            'uint16': np.uint16,
            'int16_t': np.int16,
            'int16': np.int16,
            'int24_t': np.int32,
            'int24': np.int32,
            'uint32_t': np.uint32,
            'uint32': np.uint32,
            'int32_t': np.int32,
            'int32': np.int32,
            'float_t': np.float32,
            'float': np.float32,
            'float32': np.float32,
            'double_t': np.double,
            'double': np.double,
        }
        return switcher.get(check_type, "error")
    
    @staticmethod
    # Function to convert a buffer of bytes into a NumPy array of 24-bit integers
    def int24_buffer_to_int32_buffer(buffer):
        # Ensure the buffer length is a multiple of 3 bytes (24 bits)
        if len(buffer) % 3 != 0:
            raise ValueError("Buffer length must be a multiple of 3 bytes")

        # Convert the buffer to a NumPy array of uint8
        byte_array = np.frombuffer(buffer, dtype=np.uint8)

        # Reshape the byte array to have 3 columns (each row is a 24-bit integer)
        byte_array = byte_array.reshape(-1, 3)

        # Convert the 3-byte rows into 32-bit integers
        int32_array = (byte_array[:, 2].astype(np.uint32) << 16) | \
                      (byte_array[:, 1].astype(np.uint32) << 8) | \
                      byte_array[:, 0].astype(np.uint32)

        # Handle sign extension for 24-bit integers
        int32_array = np.where(int32_array & 0x800000, int32_array | 0xFF000000, int32_array)

        return int32_array.tobytes()
    

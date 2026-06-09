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
"""
STDatalog core data classes and type definitions.

Provides:
- TypeEnum: Supported logical data types used by the SDK.
- DataClass: Minimal container for component metadata and associated data.
- RawDataClass: Container for raw sensor data with sub-sensor descriptor/status context.

These lightweight structures standardize data representation across the SDK
    and do not perform I/O or validation.
"""

from enum import Enum

class TypeEnum(Enum):
    """
    Data types supported in STDatalog data classes.
    Enumerates the possible data types for sensor data representation.
    """
    STRING = "string"
    INTEGER = "integer"
    DOUBLE = "double"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ENUM = "enum"
    OBJECT = "object"

class DataClass(object):
    """
    Base class for data representations in STDatalog.
    Encapsulates common attributes for data classes.

    Attributes:
        comp_name (str): Name of the component.
        data: The actual data associated with the component.
    """
    def __init__(self, comp_name, data):
        """
        Initialize a DataClass instance.
        Args:
            comp_name (str): Name of the component.
            data: The actual data associated with the component.
        """
        self.comp_name = comp_name
        self.data = data
class RawDataClass(object):
    """
    Class for raw data representation in STDatalog.
    Encapsulates raw data attributes.
    Attributes:
        p_id: Parameter ID.
        ssd: Sub-sensor descriptor.
        sss: Sub-sensor status.
        data: The actual raw data.
    """
    def __init__(self, p_id, ssd, sss, data):
        """
        Initialize a RawDataClass instance.
        Args:
            p_id: Parameter ID.
            ssd: Sub-sensor descriptor.
            sss: Sub-sensor status.
            data: The actual raw data.
        """
        self.p_id = p_id
        self.sss = sss
        self.ssd = ssd
        self.data = data

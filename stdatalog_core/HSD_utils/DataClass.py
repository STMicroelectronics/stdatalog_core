
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

from enum import Enum

class TypeEnum(Enum):
    STRING = "string"
    INTEGER = "integer"
    DOUBLE = "double"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ENUM = "enum"
    OBJECT = "object"

class DataClass(object):
    def __init__(self, comp_name, data):
        self.comp_name = comp_name
        self.data = data
class RawDataClass(object):
    def __init__(self, p_id, ssd, sss, data):
        self.p_id = p_id
        self.sss = sss
        self.ssd = ssd
        self.data = data

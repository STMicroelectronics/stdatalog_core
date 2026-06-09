# *****************************************************************************
#  * @file    HSDLink_utils.py
#  * @author  SRA
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

# Allowed vendor-ids for ST Datalog devices (hexadecimal, capital letters, no "0x" prefix).
# "0483": STMicroelectronics vendor-id.
ALLOWED_SERIAL_VID_HEX = ["0483"]

# Allowed product-ids for ST Serial Datalog devices (hexadecimal, capital letters, no "0x" prefix).
# "374E": SDATALOG on STEVAL-MKBOXPRO over STLINK-V3MINI.
# "3754": SDATALOG on STEVAL-MKBOXPRO over STLINK-V3MINIE.
# "5740": SDATALOG_RUST on STEVAL-MKBOXPRO over USB.
ALLOWED_SERIAL_PID_HEX = ["374E", "3754", "5740"]

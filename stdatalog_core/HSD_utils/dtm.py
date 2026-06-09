
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
Utilities for handling Device Template Models (DTMs) in the HSDatalog SDK.
This module exposes HSDatalogDTM, a helper to load a DTM (in DTDL JSON format)
from disk and register it into the device catalog via DeviceCatalogManager.
Key functionality:
- upload_custom_dtm(custom_dtm): Given a tuple (board_id, fw_id, dtm_path),
    reads the JSON file, serializes it, and calls
    DeviceCatalogManager.add_dtdl_model(board_id, fw_id, dtm_filename, dtm_json).
Parameters:
- custom_dtm:
    - board_id: Identifier of the target board in the catalog (int or str).
    - fw_id: Firmware identifier for the board (int or str).
    - dtm_path: Filesystem path to the DTM JSON file.
Behavior and error handling:
- Validates the file path and attempts to load/serialize JSON content.
- On invalid path or read/parse errors, logs an error and returns without raising.
- No action is performed if custom_dtm is None.
Dependencies:
- stdatalog_core.HSD_utils.logger for error logging.
- stdatalog_pnpl.DTDL.device_template_manager.DeviceCatalogManager for catalog updates.
Example:
        >>> HSDatalogDTM.upload_custom_dtm((123, 1, "/path/to/device_dtm.json"))
"""

import os
import json
import stdatalog_core.HSD_utils.logger as logger
from stdatalog_pnpl.DTDL.device_template_manager import DeviceCatalogManager
log = logger.setup_applevel_logger(is_debug = False, file_name= "app_debug.log")

class HSDatalogDTM:
    """
    Utility class for handling Device Template Models (DTMs).
    
    Methods:
        - upload_custom_dtm(custom_dtm): Uploads a custom DTM to the DeviceCatalogManager.
    """
    @staticmethod
    def upload_custom_dtm(custom_dtm):
        """
        Upload a custom Device Template Model (DTM) to the DeviceCatalogManager.

        :param custom_dtm: A tuple containing (board_id, fw_id, dtm_path).
        """
        if custom_dtm is None:
            return

        board_id, fw_id, dtm_path = custom_dtm

        # Validate path before attempting to read
        if not (os.path.isfile(dtm_path) and os.path.exists(dtm_path)):
            log.error("Invalid input custom Device Template Model path: %s", dtm_path)
            return

        try:
            with open(dtm_path, encoding="utf-8") as f:
                device_json_dict = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Failed to read/parse custom DTM at %s: %s", dtm_path, exc)
            return

        dtm_filename = os.path.basename(dtm_path).split(".")[0]
        dtm_json_str = json.dumps(device_json_dict)

        DeviceCatalogManager.add_dtdl_model(
            board_id,
            fw_id,
            dtm_filename,
            dtm_json_str,
        )

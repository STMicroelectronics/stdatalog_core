# *****************************************************************************
#  * @file    HSDatalog_v2.py
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

"""High-level offline datalog analyzer and plotting utilities for STDatalog v2.

Note: This module is intentionally large and aggregates analysis, parsing and plotting
for multiple component categories. Some Pylint rules are relaxed to accommodate
the monolithic design maintained for backward compatibility.

Module purpose:
    - Provide the HSDatalog_v2 class for managing device models, acquisition info,
        and data analysis for STDatalog acquisitions.
    - Load device configuration (device_config.json), acquisition info (acquisition_info.json),
        and optional ISPU output format descriptors to reconstruct data semantics.
    - Parse raw .dat files produced by STDatalog firmware, handling per-interface data packet
        structure and embedded counter/timestamp protocol.
    - Convert binary streams into NumPy arrays and pandas/Dask DataFrames with proper units,
        labels, sensitivity scaling, and tag overlays.
    - Provide plotting helpers for MEMS, audio, presence, light, ranging (ToF), and algorithm (FFT)
        outputs, with optional spectrograms and interactive Dash serving.
Design notes:
    - Class methods are organized into device loading, data parsing, analysis, and plotting.
    - Uses stdatalog_pnpl for DTDL device template management and stdatalog_core utilities for
        file handling, type conversion, and logging.
    - Data integrity checks and timestamp recovery are implemented for robust parsing.
Error handling:
    - Raises specific exceptions for missing device models, acquisition info, or sensor models.
Key concepts:
    - Component types: SENSOR, ALGORITHM, ACTUATOR. Each has different payload layout,
        timestamping rules, and units.
    - Samples per timestamp (spts): frames may contain N samples with a single timestamp.
        When spts>1, timestamps are linearly interpolated between frame timestamps.
    - Data protocol: interfaces wrap data frames with a 4-byte counter. SD card stores
        counter+payload, USB/serial often provide payload directly; code removes counters
        before parsing.
    - Tags: acquisition_info.json may include software/hardware tags; plot utilities can draw
        regions.
Caveats:
    - Large files are processed in batches; memory usage depends on chunk sizes and Dask
        partitioning.
    - Device template resolution is based on board_id/fw_id and may need catalog updates.
"""

import glob
import math
import warnings


import json
import os
import struct
from threading import Thread

from datetime import datetime
from dateutil import parser
import numpy as np
import pandas as pd
try:
    from fastparquet import ParquetFile
except Exception:
    ParquetFile = None

from werkzeug.serving import make_server

from plotly.subplots import make_subplots
import plotly.graph_objects as go

from stdatalog_core.HSD.utils.plot_utils import PlotUtils
from stdatalog_core.HSD_utils.exceptions import (
    MissingDeviceModelError,
    MissingAcquisitionInfoError,
    MissingSensorModelError,
    MissingComponentModelError,
    MissingFileForSensorError,
    DataCorruptedException,
    NoDataAtIndexError,
    NSensorAxesError,
    UnsupportedSensorCategoryError,
    MissingISPUOutputDescriptorException,
    MissingTagsException,
    DataExtractionError,
    MissingPropertyError,
)
from stdatalog_core.HSD_utils import logger
from stdatalog_core.HSD.utils.cli_interaction import CLIInteraction as CLI
from stdatalog_core.HSD.utils.file_manager import FileManager
from stdatalog_core.HSD.utils.type_conversion import TypeConversion
from stdatalog_pnpl.DTDL.dtdl_utils import MC_FAST_TELEMETRY_SENSITIVITY, UnitMap
from stdatalog_pnpl.DTDL.device_template_manager import DeviceCatalogManager, DeviceTemplateManager
from stdatalog_pnpl.DTDL.device_template_model import ContentSchema, SchemaType
from stdatalog_pnpl.DTDL.dtdl_utils import (
    DTDL_SENSORS_ID_COMP_KEY,
    MC_FAST_TELEMETRY_COMP_NAME,
    MC_SLOW_TELEMETRY_COMP_NAME,
    AlgorithmTypeEnum,
    ComponentTypeEnum,
    SensorCategoryEnum,
)


log = logger.get_logger(__name__)


class ServerThread(Thread):
    """Background Dash server runner for interactive Plotly visualization.

    Responsibilities:
    - Create and serve a Dash app bound to a free local port.
    - Allow controlled shutdown from the hosting HSDatalog_v2 instance.

    Attributes:
    - server: Werkzeug WSGI server instance.
    - ctx: Flask application context pushed for thread lifetime.
    - running: bool flag indicating server loop state.

    Notes:
    - Use shutdown() to stop serving and mark running=False.
    - Port selection is done upstream; this class assumes a provided app and port.
    """

    def __init__(self, app, port=8050):
        """Initialize the server thread.

        Parameters:
        - app: Dash application instance to serve.
        - port: int local TCP port for the HTTP server (default 8050).
        """
        Thread.__init__(self)
        self.server = make_server("127.0.0.1", port, app.server)
        self.ctx = app.server.app_context()
        self.ctx.push()
        self.running = True

    def run(self):
        """Start serving the Dash app forever on the configured port."""
        log.debug(f"Starting Dash server for plotting on port {self.server.port}...")
        self.server.serve_forever()
        log.debug("Dash server has started successfully.")

    def shutdown(self):
        """Stop the server and mark the thread as no longer running."""
        log.debug(f"Shutting down Dash server for plotting on port {self.server.port}...")
        self.server.shutdown()
        self.running = False
        log.debug("Dash server has been shut down successfully.")

class HSDatalog_v2:
    """Offline analyzer for STDatalog v2 acquisitions.

    Responsibilities:
    - Load device and acquisition descriptors from disk.
    - Resolve DTDL device templates and content schemas to infer units, enums, and structure.
    - Parse raw binary datalog (.dat) streams per-component and interface rules.
    - Construct DataFrames and Dask DataFrames for analysis/plotting.
    - Provide rich plotting for multiple sensor categories and algorithms.

    Lifecycle:
    1) __init__: optionally updates catalog; if acquisition_folder is provided, loads models/files.
    2) get_data_and_timestamps_batch: reads and decodes .dat with protocol handling.
    3) __process_datalog: converts raw bytes to numerical arrays and interpolated timestamps.
    4) __to_dataframe: assembles labeled DataFrame with sensitivity and units.
    5) get_sensor_plot/get_algorithm_plot: build Plotly figures and serve via Dash.

    Attributes:
    - device_model: dict representing the current device status (components list).
    - acq_info_model: dict with acquisition_info component status (times, tags, interface).
    - mlc_ispu_output_formats: dict describing MLC/ISPU output fields (name/type) for decoding.
    - __acq_folder_path: str path to acquisition folder.
    - __checkTimestamps: bool enabling timestamp recovery for certain sensor categories.
    - data_protocol_size: int (default 4) bytes per packet for counter/integrity protocol.
    - plot_threads: list[ServerThread] of active Dash servers.

    Error handling:
    - Raises MissingDeviceModelError/MissingAcquisitionInfoError when mandatory files are absent.
    - Raises DataCorruptedException if packet counters are inconsistent
        (unless first-packet skip applies).
    - Raises MissingISPUOutputDescriptorException for ISPU without descriptor.
    """

    # Class attributes that will be used to store device model, acquisition info model,
    # and ISPU output format (if any).
    device_model = None
    acq_info_model = None
    mlc_ispu_output_formats = {}
    # Private attribute to store the path to the acquisition folder.
    __acq_folder_path = None
    __checkTimestamps = False

    def __init__(self, acquisition_folder=None, update_catalog=True):
        """
        Initialize the HSDatalog_v2 instance.
        Parameters:
            acquisition_folder (str, optional): Path to the acquisition folder.
                If provided, device and acquisition info will be loaded from this folder.
            update_catalog (bool, optional): If True, updates the device catalog
                from the online repository. Default is True.
        """
        # Update the device catalog if the update_catalog flag is set to True
        if update_catalog:
            DeviceCatalogManager.update_catalog()
        # If an acquisition folder is provided, proceed with initialization.
        if acquisition_folder is not None:
            # Attempt to find and load the device configuration from the acquisition folder.
            device_json_file_path = FileManager.find_file("device_config.json", acquisition_folder)
            if device_json_file_path is None:
                # If the device configuration file is missing, raise an error.
                raise MissingSensorModelError
            self.__load_device_from_file(device_json_file_path)
            # Attempt to find and load the acquisition information from the acquisition folder.
            try:
                acquisition_json_file_path = FileManager.find_file(
                    "acquisition_info.json", acquisition_folder
                )
                if acquisition_json_file_path is None:
                    # If the acquisition information file is missing, raise an error.
                    raise MissingAcquisitionInfoError
                self.__load_acquisition_info_from_file(acquisition_json_file_path)

                self.mlc_ispu_output_formats = self.extract_sensor_outputs_from_jsons(
                    acquisition_folder
                )
                
                # ispu_output_json_file_path = FileManager.find_file(
                #     "ispu_output_format.json", acquisition_folder
                # )
                # if ispu_output_json_file_path is not None:
                #     self.__load_ispu_output_format(ispu_output_json_file_path)                

            except MissingAcquisitionInfoError:
                # Log an error and raise it if the acquisition information file is missing.
                log.error("No acquisition_info.json file in your Acquisition folder")
                raise
            except MissingDeviceModelError:
                # Raise an error if the device model file is missing.
                raise
        else:
            # If no acquisition folder is provided, log a warning and set the device model and
            # acquisition info to None.
            log.warning("Acquisition folder not provided.")
            self.device_model = None
            self.acq_info_model = None

        # Store the acquisition folder path in a private attribute.
        self.__acq_folder_path = acquisition_folder
        # Data integrity ptocol counter byte size
        self.data_protocol_size = 4
        # Plotting server threads
        self.plot_threads: list[ServerThread] = []
        self.is_datalog2 = True

    # ========================================================================================#
    ### Data Analisys ########################################################################
    # ========================================================================================#

    ### ==> Debug ###
    def enable_timestamp_recovery(self, status):
        """
        Enable or disable timestamp recovery for sensors without embedded timestamps.
        Parameters:
            status (bool): If True, enables timestamp recovery; if False, disables it.
        """
        self.__checkTimestamps = status

    ### Debug <== ###
    def __load_device_from_file(self, device_json_file_path, device_id=0):
        """
        Load and set the device model from device_config.json.

        Parameters:
        - device_json_file_path: str path to device_config.json
        - device_id: int index in "devices" array (default 0)

        Behavior:
        - Reads JSON (handling trailing NUL if present).
        - Delegates to __load_device to resolve template and enrich status.
        - Sets self.device_model.

        Raises:
        - MissingDeviceModelError: if device template resolution fails.
        """
        try:
            with open(device_json_file_path, encoding="UTF-8") as f:
                file_content = f.read()
                if file_content[-1] == "\x00":
                    device_json_dict = json.loads(file_content[:-1])
                else:
                    device_json_dict = json.loads(file_content)
            # device_json_str = json.dumps(device_json_dict)
            f.close()
            self.__load_device(device_json_dict, device_id, True)
        except MissingDeviceModelError as e:
            raise e

    def __load_acquisition_info_from_file(self, acq_info_json_file_path):
        """
        Load acquisition_info.json and set acq_info_model.

        Parameters:
        - acq_info_json_file_path: str path to acquisition_info.json

        Behavior:
        - Reads JSON (handling trailing NUL if present).
        - acq_info_model gets a plain dict clone of the file content.

        Raises:
        - MissingAcquisitionInfoError on read/parse failures.
        """
        try:
            with open(acq_info_json_file_path, encoding="UTF-8") as f:
                file_content = f.read()
                if file_content[-1] == "\x00":
                    acq_info_json_dict = json.loads(file_content[:-1])
                else:
                    acq_info_json_dict = json.loads(file_content)
            acq_info_json_str = json.dumps(acq_info_json_dict)
            f.close()
            self.acq_info_model = json.loads(acq_info_json_str)
        except:
            raise MissingAcquisitionInfoError

    def __get_sensor_unit_from_dtdl(self, prop_w_unit_name, comp_dtdl_contents):
        """
        Resolve a unit string from DTDL content for a given property.

        Parameters:
        - prop_w_unit_name: str property name (e.g., 'fs', 'aop')
        - comp_dtdl_contents: list of DTDL content entries

        Returns:
        - str|None: unit or display_unit (localized EN fallback) if available, else None.
        """
        prop = [c for c in comp_dtdl_contents if c.name == prop_w_unit_name]
        if prop is not None and len(prop) > 0:
            unit = prop[0].unit
            if unit is not None:
                return unit
            display_unit = prop[0].display_unit
            if display_unit is not None:
                return display_unit if isinstance(display_unit, str) else display_unit.en
        return None

    # def __load_ispu_output_format(self, ispu_output_format_file_path):
    #     """
    #     Load ISPU output format descriptor JSON from file.

    #     Parameters:
    #     - ispu_output_format_file_path: str path to ispu_output_format.json

    #     Sets:
    #     - self.ispu_output_format as dict with "output" entries {name,type}.
    #     """
    #     with open(ispu_output_format_file_path, encoding="UTF-8") as f:
    #         file_content = f.read()
    #         if file_content[-1] == "\x00":
    #             ispu_out_json_dict = json.loads(file_content[:-1])
    #         else:
    #             ispu_out_json_dict = json.loads(file_content)
    #     ispu_out_json_str = json.dumps(ispu_out_json_dict)
    #     f.close()
    #     self.ispu_output_format = json.loads(ispu_out_json_str)

    @staticmethod
    def __convert_prop_enum_in_value(prop_enum_index, prop_content):
        """
        Convert DTDL enum index into display value, numeric if applicable.

        Parameters:
        - prop_enum_index: int index in enum_values
        - prop_content: DTDL content with schema.type == ENUM

        Returns:
        - str|float: human-readable value; if value_schema == integer and numeric, returns float.
        """
        enum_dname = prop_content.schema.enum_values[prop_enum_index].display_name
        value = enum_dname if isinstance(enum_dname, str) else enum_dname.en
        if prop_content.schema.value_schema.value == "integer":
            num_value = value.replace(",", "").replace(".", "")
            if num_value.isnumeric():
                return float(value.replace(",", "."))
        return value

    def __convert_enums_in_values(self, comp_dtdl_contents, comp_status):
        """
        Replace enum indices with human-readable values in a component status dict.

        Parameters:
        - comp_dtdl_contents: list of DTDL contents describing properties
        - comp_status: dict component current status to mutate
        """
        for prop in comp_dtdl_contents:
            if (
                prop.schema is not None
                and isinstance(prop.schema, ContentSchema)
                and prop.schema.type == SchemaType.ENUM
            ):
                enum_index = comp_status[prop.name]
                prop_value = self.__convert_prop_enum_in_value(enum_index, prop)
                comp_status[prop.name] = prop_value

    def __load_device(self, device_dict, device_id=0, from_file=True):
        """
        Resolve DTDL template and enrich component statuses with units and enum values.

        Parameters:
        - device_dict: dict with either {'devices': [...]} or a single device dict
        - device_id: int select index if from_file=True
        - from_file: bool whether input is device_config.json format

        Behavior:
        - Picks device_model and queries DeviceCatalog for proper template by board_id/fw_id.
        - For MEMS/AUDIO sensors adds unit based on fs/aop; converts enum properties to values.
        - Handles retrocompatibility for older "sensors" template ids.

        Raises:
        - MissingDeviceModelError if template could not be found.
        """
        if from_file:
            self.device_model = device_dict["devices"][device_id]
        else:
            self.device_model = device_dict

        board_id = hex(self.device_model["board_id"])
        fw_id = hex(self.device_model["fw_id"])

        dev_template_json = DeviceCatalogManager.query_dtdl_model(board_id, fw_id)
        if isinstance(dev_template_json, dict):
            if dev_template_json == {}:
                raise MissingDeviceModelError(board_id, fw_id)
            components = self.device_model.get("components")
            fw_name = None
            for c in components:
                if c.get("firmware_info") is not None:
                    fir_info = c.get("firmware_info")
                    if fir_info.get("fw_name") is not None:
                        fw_name = fir_info.get("fw_name")
            # fw_name = self.device_model.get("components").get("firmware_info").get("fw_name")
            if fw_name is not None:
                splitted_fw_name = fw_name.lower().split("-")
                reformatted_fw_name = "".join(
                    [splitted_fw_name[0]] + [f.capitalize() for f in splitted_fw_name[1:]]
                )
                for dt in dev_template_json:
                    if reformatted_fw_name.lower() in dev_template_json[dt][0].get("@id").lower():
                        dev_template_json = dev_template_json[dt]
                        break
        dt_manager = DeviceTemplateManager(dev_template_json)
        self.components_dtdl = dt_manager.get_components()
        for comp_name in self.components_dtdl.keys():
            comp_status = [
                c for c in self.device_model.get("components") if list(c.keys())[0] == comp_name
            ]
            if len(comp_status) > 0:
                comp_status = comp_status[0].get(comp_name)
                comp_dtdl_contents = [c for c in self.components_dtdl[comp_name].contents]
                c_type = comp_status.get("c_type")
                if c_type == ComponentTypeEnum.SENSOR.value:
                    s_category = comp_status.get("sensor_category")
                    if s_category == SensorCategoryEnum.ISENSOR_CLASS_MEMS.value:
                        comp_status["unit"] = self.__get_sensor_unit_from_dtdl(
                            "fs", comp_dtdl_contents
                        )
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_AUDIO.value:
                        comp_status["unit"] = self.__get_sensor_unit_from_dtdl(
                            "aop", comp_dtdl_contents
                        )
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA.value:
                        pass
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    else:  # Retrocompatibility
                        if (
                            ":" + DTDL_SENSORS_ID_COMP_KEY + ":"
                            in self.components_dtdl[comp_name].id
                        ):  # "sensors":
                            s_info_contents = [
                                c
                                for c in self.components_dtdl[comp_name].contents
                                if c.name == "odr" or c.name == "fs" or c.name == "aop"
                            ]
                            comp_status = [
                                x
                                for x in self.device_model["components"]
                                if list(x.keys())[0] == comp_name
                            ]
                            if len(comp_status) > 0:
                                if len(s_info_contents) > 0:
                                    for sc in s_info_contents:
                                        if (
                                            sc.name == "fs" or sc.name == "aop"
                                        ) and sc.unit is not None:
                                            comp_status[0][comp_name]["unit"] = sc.unit
                                        elif (
                                            sc.name == "fs" or sc.name == "aop"
                                        ) and sc.display_unit is not None:
                                            display_unit = (
                                                sc.display_unit
                                                if isinstance(sc.display_unit, str)
                                                else sc.display_unit.en
                                            )
                                            comp_status[0][comp_name]["unit"] = display_unit
                                        if (
                                            from_file
                                            and sc.schema is not None
                                            and isinstance(sc.schema, ContentSchema)
                                            and sc.schema.type == SchemaType.ENUM
                                        ):
                                            enum_index = comp_status[0][comp_name][sc.name]
                                            prop_value = float(
                                                self.__convert_prop_enum_in_value(enum_index, sc)
                                            )
                                            comp_status[0][comp_name][sc.name] = prop_value
                                else:
                                    if "unit" not in comp_status[0][comp_name]:
                                        comp_status[0][comp_name]["unit"] = ""
        log.debug(f"Device Model: {self.device_model}")

    def get_file_dimension(self, component_name):
        """Return file size in bytes for the given component's .dat file, or None if missing."""
        filepath = os.path.join(self.__acq_folder_path, f"{component_name}.dat")
        if os.path.isfile(filepath):
            return os.path.getsize(filepath)
        return None

    def get_data_protocol_size(self):
        """Return the current data protocol counter size in bytes (default 4)."""
        return self.data_protocol_size

    def get_acquisition_path(self):
        """Return the acquisition folder absolute or relative path."""
        return self.__acq_folder_path

    def __normalize_ai_sensor_names(self, sensor_name):
        if isinstance(sensor_name, str):
            return [sensor_name.strip().lower()] if sensor_name.strip() else []
        if isinstance(sensor_name, list):
            return [str(name).strip().lower() for name in sensor_name if str(name).strip()]
        return []

    def __get_ai_component_names(self):
        if self.device_model is None:
            return []

        component_names = []
        for component in self.device_model.get("components", []):
            comp_name = list(component.keys())[0]
            if comp_name.endswith("_mlc") or comp_name.endswith("_ispu"):
                component_names.append(comp_name)
        return component_names

    def __resolve_ai_component_names(self, sensor_name, outputs, source_path):
        normalized_names = self.__normalize_ai_sensor_names(sensor_name)
        ai_components = self.__get_ai_component_names()

        core_values = {
            str(output.get("core")).upper()
            for output in outputs
            if isinstance(output, dict) and output.get("core") in ["MLC", "ISPU"]
        }
        core = None
        if len(core_values) == 1:
            core = next(iter(core_values))
        elif len(core_values) > 1:
            log.warning(
                "Mixed output cores found in %s. Falling back to component-name inference.",
                source_path,
            )

        candidate_components = []
        for comp_name in ai_components:
            comp_name_lower = comp_name.lower()
            if comp_name_lower in normalized_names:
                candidate_components.append(comp_name)
                continue

            for normalized_name in normalized_names:
                if comp_name_lower.startswith(normalized_name + "_"):
                    candidate_components.append(comp_name)
                    break

        candidate_components = list(dict.fromkeys(candidate_components))

        if core is None and len(candidate_components) == 1:
            if candidate_components[0].endswith("_mlc"):
                core = "MLC"
            elif candidate_components[0].endswith("_ispu"):
                core = "ISPU"

        if core is None and len(outputs) == 0:
            core = "MLC"

        if core == "MLC":
            candidate_components = [comp for comp in candidate_components if comp.endswith("_mlc")]
        elif core == "ISPU":
            candidate_components = [comp for comp in candidate_components if comp.endswith("_ispu")]

        return candidate_components, core

    def __register_ai_output_entry(self, result, comp_name, outputs, core):
        if comp_name in result:
            return

        result[comp_name] = {
            "outputs": outputs if isinstance(outputs, list) and len(outputs) > 0 else None,
            "core": core,
        }

    def __load_legacy_ispu_output_format(self, result, json_file, data):
        outputs = data.get("output")
        if not isinstance(outputs, list) or len(outputs) == 0:
            return

        ispu_components = [
            comp_name
            for comp_name in self.__get_ai_component_names()
            if comp_name.endswith("_ispu") and comp_name not in result
        ]

        if len(ispu_components) == 1:
            self.__register_ai_output_entry(result, ispu_components[0], outputs, "ISPU")
        elif len(ispu_components) > 1:
            log.warning(
                "Legacy ISPU output format file %s is ambiguous across multiple ISPU components.",
                json_file,
            )
    
    def extract_sensor_outputs_from_jsons(self, acquisition_folder):
        """
        Search for JSON files in the acquisition_folder, extract sensors and their outputs.
        For each sensor, use its "name" as key in a dictionary, and fill with "outputs".
        If in outputs the "core" item is "ISPU" or "MLC", add "core":"ISPU"/"core":"MLC" to the dict.
        Returns:
            dict: {sensor_name: {"outputs": [...], "core": "ISPU"/"MLC" (if present)}}
        """
        result = {}
        json_files = glob.glob(os.path.join(acquisition_folder, "*.json"))
        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Look for "sensors" key at any level
                def find_sensors(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k == "sensors" and isinstance(v, list):
                                return v
                            found = find_sensors(v)
                            if found:
                                return found
                    elif isinstance(obj, list):
                        for item in obj:
                            found = find_sensors(item)
                            if found:
                                return found
                    return None
                sensors = find_sensors(data)
                if sensors:
                    for sensor in sensors:
                        if not isinstance(sensor, dict):
                            continue

                        outputs = sensor.get("outputs")
                        if outputs is None:
                            outputs = []
                        if not isinstance(outputs, list):
                            continue

                        candidate_components, core = self.__resolve_ai_component_names(
                            sensor.get("name"), outputs, json_file
                        )
                        for comp_name in candidate_components:
                            self.__register_ai_output_entry(result, comp_name, outputs, core)
                else:
                    self.__load_legacy_ispu_output_format(result, json_file, data)
            except Exception:
                continue
        return result

    def get_device(self):
        """
        Retrieves the current device model from the HSDatalog instance.
        This method returns the device model that has been set for the instance, which contains
        information about the device.

        :return: The device model object or dictionary that holds the device's information.
        """
        # Return the device_model attribute of the HSDatalog instance.
        # This attribute should hold the current device's information.
        return self.device_model

    def set_device(self, new_device, device_id=0, from_file=True):
        """Set a new device model and reload templates/enriched status.

        Parameters:
        - new_device: dict either device_config.json-like or plain device dict
        - device_id: int index when using file format
        - from_file: bool if new_device comes from file format

        Side effects:
        - Logs a summary line including alias, fw_name, fw_version and part/serial number if known.
        """
        if from_file:
            # Update the device_model attribute with the first device from the new_device dictionary
            self.device_model = new_device["devices"][device_id]
        else:
            self.device_model = new_device
        # Call the private method '__load_device' to load the device configuration
        self.__load_device(new_device, device_id, from_file)
        # Retrieve firmware information from the device
        fw_info = self.get_firmware_info()["firmware_info"]
        # Log the device information, including alias, firmware name, and version
        # Check if part number is available and include it in the log message
        if "part_number" in fw_info:
            log.info(
                (
                    f"Device [{fw_info['alias']}] - {fw_info['fw_name']} "
                    f"v{fw_info['fw_version']} "
                    f"sn:{fw_info['part_number']} loaded correctly!"
                )
            )
        # If part number is not available, check for serial number and include it in the log message
        elif "serial_number" in fw_info:
            log.info(
                (
                    f"Device [{fw_info['alias']}] - {fw_info['fw_name']} "
                    f"v{fw_info['fw_version']} "
                    f"sn:{fw_info['serial_number']} loaded correctly!"
                )
            )
        # If neither part number nor serial number is available, log the information without them
        else:
            log.info(
                (
                    f"Device [{fw_info['alias']}] - {fw_info['fw_name']} "
                    f"v{fw_info['fw_version']} loaded correctly!"
                )
            )

    def get_device_info(self):
        """Return the DeviceInformation component dict from the device model."""
        return self.get_component("DeviceInformation")

    def get_firmware_info(self):
        """Return the firmware_info component dict from the device model, or None."""
        return self.get_component("firmware_info")

    def set_device_info(self, new_device_info):
        """Set a device_info object/dict (v2 keeps JSON), without validation."""
        self.device_info = new_device_info

    def get_component(self, component_name):
        """Return a component status dict by name, or raise if not found.

        Raises:
        - MissingComponentModelError if component_name is not present.
        - MissingDeviceModelError if device_model is not loaded.
        """
        if self.device_model is not None:
            components = self.device_model["components"]
            for c in components:
                if list(c)[0] == component_name:
                    return c
            log.error(f"No Model loaded for {component_name} Component")
            raise MissingComponentModelError
        log.error("No Device Model loaded!")
        raise MissingDeviceModelError

    def get_sensor_list(self, type_filter="", only_active=False):
        """List sensor components with optional type filter and enabled-only listing.

        Parameters:
        - type_filter: str lowercase tail of component name (e.g., 'acc', 'mic')
        - only_active: bool include only enabled sensors

        Returns:
        - list[dict]: component entries as in device_model['components'].
        """
        active_sensors = []
        sensor_list = self.device_model["components"]
        for s in sensor_list:
            for element in s[list(s)[0]]:
                if (
                    element == "c_type"
                    and s[list(s)[0]]["c_type"] == ComponentTypeEnum.SENSOR.value
                ):
                    if type_filter == "":
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]["enable"] is True:
                                active_sensors.append(s)
                        else:
                            active_sensors.append(s)
                    else:
                        sensor_type = str(list(s)[0]).lower().rsplit("_", maxsplit=1)[-1]
                        if only_active:
                            if (
                                "enable" in s[list(s)[0]]
                                and s[list(s)[0]]["enable"] is True
                                and sensor_type == type_filter.lower()
                            ):
                                active_sensors.append(s)
                        else:
                            if sensor_type == type_filter.lower():
                                active_sensors.append(s)
        return active_sensors

    def get_algorithm_list(self, type_filter="", only_active=False):
        """List algorithm components optionally filtered and enabled-only."""
        active_algos = []
        algo_list = self.device_model["components"]
        for s in algo_list:
            for element in s[list(s)[0]]:
                if (
                    element == "c_type"
                    and s[list(s)[0]]["c_type"] == ComponentTypeEnum.ALGORITHM.value
                ):
                    if type_filter == "":
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]["enable"] is True:
                                active_algos.append(s)
                        else:
                            active_algos.append(s)
                    else:
                        sensor_type = str(list(s)[0]).lower().rsplit("_", maxsplit=1)[-1]
                        if only_active:
                            if (
                                "enable" in s[list(s)[0]]
                                and s[list(s)[0]]["enable"] is True
                                and sensor_type == type_filter.lower()
                            ):
                                active_algos.append(s)
                        else:
                            if sensor_type == type_filter.lower():
                                active_algos.append(s)
        return active_algos

    def get_actuator_list(self, only_active=False):
        """List actuator components, optionally only enabled ones."""
        active_actuators = []
        actuator_list = self.device_model["components"]
        for ac in actuator_list:
            for element in ac[list(ac)[0]]:
                if (
                    element == "c_type"
                    and ac[list(ac)[0]]["c_type"] == ComponentTypeEnum.ACTUATOR.value
                ):
                    if only_active:
                        if "enable" in ac[list(ac)[0]] and ac[list(ac)[0]]["enable"] is True:
                            active_actuators.append(ac)
                    else:
                        active_actuators.append(ac)
        return active_actuators

    def get_sw_tag_classes(self):
        """Return software tag classes filtered by key.
        Looks into `tags_info` and returns entries whose keys contain 'sw_tag'.
        """
        if self.device_model is not None:
            tags_info_dict = self.get_component("tags_info")
            if tags_info_dict is not None:
                return {
                    key: value
                    for key, value in tags_info_dict["tags_info"].items()
                    if "sw_tag" in key
                }
            return {}
        return None

    def get_hw_tag_classes(self):
        """Return dict of hardware tag classes from tags_info or None if missing."""
        if self.device_model is not None:
            tags_info_dict = self.get_component("tags_info")
            if tags_info_dict is not None:
                return {
                    key: value
                    for key, value in tags_info_dict["tags_info"].items()
                    if "hw_tag" in key
                }
        return None

    def get_acquisition_info(self):
        """Return the acquisition_info model dict previously loaded from file."""
        return self.acq_info_model

    def set_acquisition_info(self, new_acquisition_info):
        """Set acquisition info dict (no validation)."""
        self.acq_info_model = new_acquisition_info

    def get_acquisition_interface(self):
        """Return numeric interface id from acquisition_info (0:SD,1:USB,2:BLE,3:Serial)."""
        return self.acq_info_model["interface"]

    def get_acquisition_label_classes(self):
        """Return sorted unique tag labels from acquisition_info, or []/None with warnings."""
        if self.acq_info_model is not None:
            if "tags" in self.acq_info_model:
                return sorted(set(dic["l"] for dic in self.acq_info_model["tags"]))
            log.warning("No defined tag classes in Acquisition Information Component status.")
            return []
        log.warning("Empty Acquisition Info model.")
        return None

    def get_time_tags(self, which_tags=None):
        """
        Build time-span tags relative to acquisition start.

        Parameters:
        - which_tags: Optional[list[str]] labels to include; None for all.

        Returns:
        - list[dict]: entries {label, time_start, time_end} in seconds from start.

        Notes:
        - Associates each 'start' tag with the nearest subsequent 'end' tag of same label.
        - If no end tag found, end time defaults to acquisition end.
        """
        time_labels = []
        if self.acq_info_model is not None:
            acq_start_time = self.acq_info_model["start_time"]
            acq_end_time = self.acq_info_model["end_time"]
            self.s_t = datetime.strptime(acq_start_time, "%Y-%m-%dT%H:%M:%S.%fZ")

            # Extract tags from acquisition info model status
            tags = self.acq_info_model.get("tags", [])
            if tags == []:
                log.warning("No defined tags in Acquisition Information Component status.")
                return []

            if which_tags is not None:
                tags = [tag for tag in tags if tag["l"] in which_tags]

            for lbl in self.get_acquisition_label_classes():
                # start_time, end_time are vectors
                # with the corresponding 't' entries in DataTag-json
                start_time = np.array([t["ta"] for t in tags if t["l"] == lbl and t["e"]])
                end_time = np.array([t["ta"] for t in tags if t["l"] == lbl and not t["e"]])
                # now must associate at each start tag the appropriate end tag
                # (some may be missing because of errors in the tagging process)
                for tstart in start_time:
                    tag = {}
                    jj = [i for (i, n) in enumerate(end_time) if n >= tstart]
                    if jj:
                        tend = end_time[min(jj)]
                    else:
                        tend = acq_end_time  # if no 'end tag' found the end is eof
                    tag["label"] = lbl
                    tag["time_start"] = (
                        datetime.strptime(tstart, "%Y-%m-%dT%H:%M:%S.%fZ") - self.s_t
                    ).total_seconds()
                    tag["time_end"] = (
                        datetime.strptime(tend, "%Y-%m-%dT%H:%M:%S.%fZ") - self.s_t
                    ).total_seconds()
                    time_labels.append(tag)
            return time_labels
        log.error("Empty Acquisition Info model.")
        raise MissingAcquisitionInfoError

    # Helper function to convert ISO8601 time strings to seconds
    def get_seconds_from_ISO8601(self, start_time_str, end_time_str):
        """Convert two ISO8601 time strings to duration in seconds."""
        start_time = parser.isoparse(start_time_str)
        end_time = parser.isoparse(end_time_str)
        duration = (end_time - start_time).total_seconds()
        return duration

    # Get tags dictionary list from acquisition_info.json file
    def get_tags(self):
        """
        Return tags grouped by label with start/end (seconds from acq start).

        Returns:
        - list[{'label': str, 'times': list[tuple(float,float)]}]
        """
        tags = []
        acq_start_time = self.acq_info_model["start_time"]
        acq_end_time = self.acq_info_model["end_time"]
        tags_array = self.acq_info_model["tags"]
        acq_duration = self.get_seconds_from_ISO8601(acq_start_time, acq_end_time)

        tag_labels = []
        for i in range(len(tags_array)):
            start_tag = tags_array[i]
            if start_tag["e"]:
                tag_label = start_tag["l"]
                tag_start_time = start_tag["ta"]
                tag_start_seconds = self.get_seconds_from_ISO8601(acq_start_time, tag_start_time)
                tag_end_seconds = acq_duration
                for j in range(i, len(tags_array)):
                    end_tag = tags_array[j]
                    if end_tag["l"] == tag_label and not end_tag["e"]:
                        tag_end_time = end_tag["ta"]
                        tag_end_seconds = self.get_seconds_from_ISO8601(
                            acq_start_time, tag_end_time
                        )
                        break
                if tag_label in tag_labels:
                    # Already present in tags list --> ADD tag_times!
                    for tag in tags:
                        if tag["label"] == tag_label:
                            tag["times"].append((tag_start_seconds, tag_end_seconds))
                else:
                    tag_labels.append(tag_label)
                    tags.append(
                        {"label": tag_label, "times": [(tag_start_seconds, tag_end_seconds)]}
                    )

        return tags

    def __get_active_mc_telemetries_names(self, ss_stat, comp_name):
        """Return active Motor Control telemetry names for slow/fast streams."""
        if comp_name == "slow_mc_telemetries":
            desc_telemetry = ss_stat.get("st_ble_stream")
            return [
                st
                for st in desc_telemetry
                if isinstance(desc_telemetry[st], dict) and desc_telemetry[st].get("enable") is True
            ]
        elif comp_name == "fast_mc_telemetries":
            return [
                k
                for k in ss_stat.keys()
                if isinstance(ss_stat[k], dict)
                and k != "sensitivity"
                and ss_stat[k]["enabled"] is True
            ]

    def __process_datalog(
        self,
        sensor_name,
        ss_stat,
        raw_data,
        dataframe_size,
        timestamp_size,
        raw_flag=False,
        start_time=None,
        prev_timestamp=None,
    ):
        """Convert raw byte buffer into (data_array, timestamps_array).

        Parameters:
        - sensor_name: str component name (used for category and special cases like ISPU/MLC)
        - ss_stat: dict component status including data_type, odr/measodr, dim,
            spts, sensitivity,...
        - raw_data: np.ndarray[uint8] payload (counters removed)
        - dataframe_size: int bytes per frame for data segment
        - timestamp_size: int bytes per frame for timestamp (0 if absent)
        - raw_flag: bool do not scale by sensitivity; keep raw type
        - start_time: Optional[float] seconds from acquisition start used for interpolation
        - prev_timestamp: Optional[float] previous frame timestamp for boundary interpolation

        Returns:
        - tuple[np.ndarray, np.ndarray]: (s_data, samples_times)

        Behavior:
        - For frames with timestamps, reads a timestamp per frame; for spts>1,
            linearly interpolates per-sample times.
        - For frames without timestamps, builds a synthetic timeline based on
            ODR/measodr and ioffset.
        - Applies sensitivity scaling for non-raw output and category-specific conversions.
        - Handles audio/MEMS/algorithm/actuator categories with different shapes.
        """

        #####################################################################
        def extract_data_and_timestamps(start_time):
            """Extract data and timestamps from raw_data buffer. This inner function
            is defined to encapsulate the logic for extracting data and timestamps
            from the raw_data buffer based on the provided parameters.
            Parameters:
            - start_time: float starting timestamp for the first frame
            Returns:
            - data: np.ndarray with extracted data
            - timestamps: list of float with extracted timestamps
            """

            sensor_name_contains_mlc_ispu = "_mlc" in sensor_name or "_ispu" in sensor_name
            check_timestamps = False
            frame_period = 0
            if c_type == ComponentTypeEnum.SENSOR.value:
                s_category = ss_stat.get("sensor_category")
                if self.__checkTimestamps is True:
                    check_timestamps = not sensor_name_contains_mlc_ispu
                else:
                    check_timestamps = False
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    odr = (
                        1 / (ss_stat.get("intermeasurement_time") / 1000)
                        if ss_stat.get("intermeasurement_time")
                        > ss_stat.get("exposure_time") / 1000 + 6
                        else (1 / ((ss_stat.get("exposure_time") / 1000 + 6) / 1000))
                    )
                    frame_period = 0 if sensor_name_contains_mlc_ispu else samples_per_ts / odr
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    frame_period = (
                        0
                        if sensor_name_contains_mlc_ispu
                        else samples_per_ts / (1 / (ss_stat.get("adc_conversion_time") / 1000000))
                    )
                else:
                    measodr = ss_stat.get("measodr")
                    if measodr is None or measodr == 0:
                        measodr = ss_stat.get("odr")
                    frame_period = 0 if sensor_name_contains_mlc_ispu else samples_per_ts / measodr
            elif c_type == ComponentTypeEnum.ALGORITHM.value:
                check_timestamps = False
                if ss_stat.get("algorithm_type") == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    fft_sample_freq = ss_stat.get("fft_sample_freq")
                    frame_period = samples_per_ts / fft_sample_freq
                else:
                    frame_period = 0
                algo_type = ss_stat.get("algorithm_type")
            elif c_type == ComponentTypeEnum.ACTUATOR.value:
                check_timestamps = False
                if "samples_per_ts" not in ss_stat:
                    frame_period = 0
                else:
                    frame_period = samples_per_ts / ss_stat.get("odr")

            # rndDataBuffer = raw_data rounded to an integer # of frames
            rnd_data_buffer = raw_data[: int(frame_size * num_frames)]

            if start_time != 0:
                timestamp_first = start_time
            else:
                timestamp_first = ss_stat.get("ioffset", 0)
            timestamps = []
            data_type = TypeConversion.get_np_dtype(data_type_string)
            data = np.zeros((data1D_per_frame * num_frames, 1), dtype=data_type)

            if timestamp_size != 0:
                for ii in range(num_frames):  # For each Frame:
                    start_frame = ii * frame_size
                    # segment_data = data in the current frame
                    segment_data = rnd_data_buffer[start_frame : start_frame + dataframe_size]
                    if data_type_string in ("int24", "int24_t"):
                        segment_data = TypeConversion.int24_buffer_to_int32_buffer(segment_data)

                    # segment_tS = ts is at the end of each frame
                    segment_ts = rnd_data_buffer[
                        start_frame + dataframe_size : start_frame + frame_size
                    ]

                    # timestamp of current frame
                    timestamps.append(np.frombuffer(segment_ts, dtype="double")[0])

                    # Data of current frame
                    data_range = slice(ii * data1D_per_frame, (ii + 1) * data1D_per_frame)
                    data[data_range, 0] = np.frombuffer(segment_data, dtype=data_type)

                    # Check Timestamp consistency
                    if check_timestamps and ii > 0:
                        delta_ts = abs(timestamps[ii] - timestamps[ii - 1])
                        if (
                            delta_ts < 0.1 * frame_period
                            or delta_ts > 10 * frame_period
                            or np.isnan(timestamps[ii])
                            or np.isnan(timestamps[ii - 1])
                        ):
                            data[data_range, 0] = 0
                            timestamps[ii] = timestamps[ii - 1] + frame_period
                            log.warning(
                                f"Sensor {sensor_name}: corrupted data at {timestamps[ii]} sec"
                            )
            else:
                if data_type_string in ("int24", "int24_t"):
                    rnd_data_buffer = TypeConversion.int24_buffer_to_int32_buffer(rnd_data_buffer)
                data = np.frombuffer(rnd_data_buffer, dtype=data_type)
                is_first_chunk = ss_stat.get("is_first_chunk", False)
                if is_first_chunk:
                    start_time = timestamp_first
                    stop_time = timestamp_first + (num_frames * frame_period)
                else:
                    start_time = timestamp_first + frame_period
                    stop_time = timestamp_first + frame_period + (num_frames * frame_period)

                timestamps = np.arange(
                    start=start_time, stop=stop_time, step=frame_period, dtype=np.float64
                )

                timestamps = timestamps[:num_frames]

            if c_type == ComponentTypeEnum.SENSOR.value:
                s_dim = ss_stat.get("dim", 1)
                if raw_flag:
                    s_data = np.reshape(
                        data, (-1, dataframe_size if "_ispu" in sensor_name else s_dim)
                    )
                else:
                    s_data = np.reshape(
                        data, (-1, dataframe_size if "_ispu" in sensor_name else s_dim)
                    ).astype(dtype=np.byte if "_ispu" in sensor_name else np.float32)
                    sensitivity = float(ss_stat.get("sensitivity", 1))
                    np.multiply(s_data, sensitivity, out=s_data, casting="unsafe")
            elif c_type == ComponentTypeEnum.ALGORITHM.value:
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    s_data = np.reshape(data, (-1, ss_stat["fft_length"])).astype(dtype=np.float32)
                if not raw_flag:
                    sensitivity = float(ss_stat.get("sensitivity", 1))
                    np.multiply(s_data, sensitivity, out=s_data, casting="unsafe")
            elif c_type == ComponentTypeEnum.ACTUATOR.value:
                if sensor_name in (MC_SLOW_TELEMETRY_COMP_NAME, MC_FAST_TELEMETRY_COMP_NAME):
                    active_fast_telemetries = self.__get_active_mc_telemetries_names(
                        ss_stat, sensor_name
                    )
                    nof_telemetries = len(active_fast_telemetries)
                    s_data = np.reshape(data, (-1, nof_telemetries)).astype(dtype=np.float32)
                if sensor_name == MC_FAST_TELEMETRY_COMP_NAME and not raw_flag:
                    scaler_current = ss_stat[MC_FAST_TELEMETRY_SENSITIVITY]["current"]
                    scaler_voltage = ss_stat[MC_FAST_TELEMETRY_SENSITIVITY]["voltage"]
                    for idx, t in enumerate(active_fast_telemetries):
                        if "i" in t:
                            s_data[:, idx] = s_data[:, idx] * scaler_current
                        elif "v" in t:
                            s_data[:, idx] = s_data[:, idx] * scaler_voltage

            if len(data) == 0:
                return [], []

            # samples_time: numpy array of 1 clock value per each data sample
            if samples_per_ts > 1:
                # initial_offset is relevant
                frames = num_frames
                ioffset = ss_stat.get("ioffset", 0)
                is_first_chunk = ss_stat.get("is_first_chunk", False)

                if start_time != 0 and is_first_chunk:
                    if prev_timestamp is not None:
                        timestamps = np.insert(timestamps, 0, prev_timestamp)
                    else:
                        timestamps = np.insert(timestamps, 0, ioffset)
                else:
                    timestamps = np.append(ioffset, timestamps)

                # NOTE! Update the ioffset with the last extracted timestamp to allow
                # eventual batch processing (this will be the start timestamp to continue
                # the linear interpolation for the next chunk)
                ss_stat["ioffset"] = timestamps[-1]
                samples_times = np.zeros((frames * samples_per_ts, 1))
                samples_times.fill(-1)

                # sample times between timestamps are linearly interpolated
                for ii in range(frames):  # For each Frame:
                    delta_ts = abs(timestamps[ii + 1] - timestamps[ii])
                    if frame_period > 0 and delta_ts > frame_period + frame_period * 0.33:
                        samples_times[ii * samples_per_ts : (ii + 1) * samples_per_ts, 0] = (
                            np.linspace(
                                timestamps[ii + 1] - frame_period,
                                timestamps[ii + 1],
                                samples_per_ts,
                                endpoint=False,
                            )
                        )
                    else:
                        samples_times[ii * samples_per_ts : (ii + 1) * samples_per_ts, 0] = (
                            np.linspace(
                                timestamps[ii], timestamps[ii + 1], samples_per_ts, endpoint=False
                            )
                        )
            else:
                # if samples_per_ts is 1, the timestamps coincides with the sample timestamp
                # initial offset and interpolation is not relevant anymore
                samples_times = np.array(timestamps).reshape(-1, 1)
                if len(timestamps) > 0:
                    # NOTE! Update the ioffset with the last extracted timestamp to allow
                    # eventual batch processing (this will be the start timestamp to continue
                    # the linear interpolation for the next chunk)
                    ss_stat["ioffset"] = timestamps[-1]

            valid_indices = samples_times != -1
            samples_times = samples_times[valid_indices.flatten()]
            s_data = s_data[valid_indices.flatten()]

            return s_data, samples_times

        #####################################################################
        # get component type
        c_type = ss_stat.get("c_type")

        # size of the frame. A frame is data + ts
        frame_size = dataframe_size + timestamp_size

        # number of frames = round down (//) len datalog // frame_size
        num_frames = len(raw_data) // frame_size

        # force int8 data_type for ISPU
        data_type_string = "int8" if "_ispu" in sensor_name else ss_stat["data_type"]
        data_type_byte_num = TypeConversion.check_type_length(data_type_string)

        # data1D_per_frame = number of data samples in 1 frame
        # must be the same as samplePerTs * number of axes
        data1D_per_frame = int(dataframe_size / data_type_byte_num)

        # samples per timestamp
        spts = ss_stat.get("samples_per_ts", {})
        if isinstance(spts, int):
            samples_per_ts = spts
        else:
            samples_per_ts = spts.get("val", 0)

        if c_type == ComponentTypeEnum.SENSOR.value:
            samples_per_ts = samples_per_ts or int(data1D_per_frame / ss_stat.get("dim", 1))
        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = ss_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                samples_per_ts = 1
        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            nof_telemetries = 0
            if sensor_name == "slow_mc_telemetries":
                desc_telemetry = ss_stat.get("st_ble_stream")
                nof_telemetries = len(
                    [
                        st
                        for st in desc_telemetry
                        if isinstance(desc_telemetry[st], dict)
                        and desc_telemetry[st].get("enable") == True
                    ]
                )
            elif sensor_name == "fast_mc_telemetries":
                nof_telemetries = len(
                    [
                        k
                        for k in ss_stat.keys()
                        if isinstance(ss_stat[k], dict)
                        and k != "sensitivity"
                        and ss_stat[k]["enabled"] == True
                    ]
                )
            if "samples_per_ts" not in ss_stat:
                samples_per_ts = dataframe_size // data_type_byte_num // nof_telemetries

        return extract_data_and_timestamps(start_time)

    def get_sensor(self, sensor_name):
        """Return the full sensor component dict by name."""
        ss_stat = self.get_component(sensor_name)
        return ss_stat

    def __get_sensor_status(self, sensor_name):
        """Return the sensor component status dict by name."""
        ss_stat = self.get_component(sensor_name)
        return ss_stat[sensor_name]

    def __get_sensor_file_path(self, sensor_name):
        """
        Return the .dat file path for the given sensor, raising if missing.

        Parameters:
        - sensor_name: str component name

        Returns:
        - str: full file path

        Raises:
        - MissingFileForSensorError if file is not found
        """
        file_path = os.path.join(self.__acq_folder_path, FileManager.encode_file_name(sensor_name))
        if not os.path.exists(file_path):
            log.error(f"No such file or directory: {file_path} found for {sensor_name} sensor")
            raise MissingFileForSensorError(file_path, sensor_name)
        return file_path

    def __get_checked_sensor_file_path(self, sensor_name):
        """
        Return the _checked.dat file path for the given sensor, raising if missing.

        Parameters:
        - sensor_name: str component name

        Returns:
        - str: full file path

        Raises:
        - MissingFileForSensorError if file is not found
        """
        file_path = os.path.join(
            self.__acq_folder_path, FileManager.encode_file_name(sensor_name + "_checked")
        )
        if not os.path.exists(file_path):
            log.error(
                (
                    f"No such file or directory: {file_path} found for "
                    f"{sensor_name + '_checked'} sensor"
                )
            )
            raise MissingFileForSensorError(file_path, sensor_name + "_checked")
        return file_path

    def remove_4bytes_every_n_optimized(self, arr, N):
        """
        Remove every 4 bytes (data protocol) from a numpy array of bytes, optimized version.

        Parameters:
        - arr: np.ndarray of uint8 representing the byte array
        - N: int, interval at which to remove 4 bytes

        Returns:
        - np.ndarray of uint8 with every 4 bytes removed at the specified interval
        """
        # Create a boolean mask for the elements to keep
        mask = np.ones(len(arr), dtype=bool)
        for start in range(0, len(arr), N):
            mask[start : start + self.data_protocol_size] = False
        # Apply the mask to get the new array
        new_arr = arr[mask]
        # Concatenate the remaining slices and return the result
        return new_arr

    def get_data_and_timestamps_batch(
        self, comp_name, comp_status, start_time=0, end_time=-1, raw_flag=False
    ):
        """
        Batch-parse a component .dat file into data and timestamps.

        Respects interface packetization when parsing the .dat file in chunks.

        Parameters:
        - comp_name: str component name
        - comp_status: dict status including interface byte sizes and data_type
        - start_time: float seconds from acquisition start
        - end_time: float seconds from acquisition start, -1 for EOF
        - raw_flag: bool do not scale by sensitivity

        Returns:
        - tuple[np.ndarray, np.ndarray]: (data, timestamps)

        Algorithm outline:
        - Compute packet sizes (data_protocol_size + interface-specific payload length).
        - Compute byte ranges for start/end based on ODR and spts to minimize I/O.
        - Stream file in packet increments, checking/using 4-byte counters to detect corruption.
        - Build a 'byte_chest' buffer of data+timestamps sliced to desired time window.
        - Delegate final reshape/scaling/timestamp interpolation to __process_datalog.
        """
        log.debug("Data & Timestamp extraction algorithm STARTED...")

        # get acquisition interface
        interface = self.acq_info_model["interface"]

        # get component type
        c_type = comp_status.get("c_type")

        data_protocol_size = self.get_data_protocol_size()
        data_packet_size = 0
        # data packet size (0:sd card, 1:usb, 2:ble, 3:serial)
        if interface == 0:
            data_packet_size = comp_status["sd_dps"] - data_protocol_size
        elif interface == 1:
            data_packet_size = comp_status["usb_dps"]
        elif interface == 2:
            data_packet_size = comp_status["ble_dps"]
        elif interface == 3:
            data_packet_size = comp_status["serial_dps"]
        else:
            log.error(f"Unknown interface: {interface}. check your device_config.json file")
            raise

        # get dat file path and size (obtained from "sensor_name + sub_sensor_type")
        file_path = self.__get_sensor_file_path(comp_name)
        file_size = os.path.getsize(file_path)

        cmplt_pkt_size = data_packet_size + data_protocol_size
        nof_data_packet = file_size // cmplt_pkt_size  # "//" math.floor equivalent #CEIL

        raw_data_array = np.array([], dtype="uint8")

        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = comp_status.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                # get FFT algo "dimensions" --> FFT Length
                s_dim = comp_status.get("fft_length")
            else:
                s_dim = comp_status.get("dim")
        else:
            # get sensor dimensions
            s_dim = comp_status.get("dim", 1)

        # get Data type byte length
        s_data_type_len = TypeConversion.check_type_length(comp_status["data_type"])

        # get samples per ts
        spts = comp_status.get("samples_per_ts", {})
        if isinstance(spts, int):
            s_samples_per_ts = spts
        else:
            if c_type == ComponentTypeEnum.ACTUATOR.value:
                s_samples_per_ts = spts.get("val", 1)
            else:
                s_samples_per_ts = spts.get("val", 0)

        if c_type == ComponentTypeEnum.SENSOR.value or (
            c_type == ComponentTypeEnum.ACTUATOR.value and "odr" in comp_status
        ):

            if s_samples_per_ts != 0:
                dataframe_byte_size = s_samples_per_ts * s_dim * s_data_type_len
                timestamp_byte_size = 8
            else:
                dataframe_byte_size = s_dim * s_data_type_len
                timestamp_byte_size = 0

            # 1 sec --> ODR samples --> ODR * dim * data_type Bytes
            odr = comp_status.get("measodr")
            if odr is None or odr == 0:
                odr = comp_status.get("odr", 1)

            tot_counters_bytes = nof_data_packet * data_protocol_size
            tot_file_data_and_times_bytes = file_size - tot_counters_bytes
            tot_timestamps_bytes = (
                tot_file_data_and_times_bytes // (dataframe_byte_size + timestamp_byte_size)
            ) * timestamp_byte_size
            tot_data_bytes = tot_file_data_and_times_bytes - tot_timestamps_bytes
            tot_data_samples = int(tot_data_bytes / (s_data_type_len * s_dim))

            start_sample_idx = math.floor(odr * start_time)
            start_data_bytes_idx = start_sample_idx * s_data_type_len * s_dim
            if s_samples_per_ts != 0:
                nof_timestamps_in_start = math.floor(start_sample_idx / s_samples_per_ts)
            else:
                nof_timestamps_in_start = 0
                sample_end = int(odr * end_time) if end_time != -1 else -1
                if sample_end > tot_data_samples or sample_end == -1:
                    sample_end = tot_data_samples
                sample_start = int(odr * start_time)
                if sample_start >= tot_data_samples:
                    return ([], [])
                # read_start_bytes = sample_start * dataframe_byte_size
                # read_end_bytes = sample_end * dataframe_byte_size

            start_data_and_times_bytes_idx = (
                start_data_bytes_idx + nof_timestamps_in_start * timestamp_byte_size
            )
            nof_counter_in_start = math.floor(start_data_and_times_bytes_idx / data_packet_size)
            start_idx = start_data_and_times_bytes_idx + nof_counter_in_start * data_protocol_size

            acq_info = self.get_acquisition_info()
            acq_start_time = parser.isoparse(acq_info["start_time"])
            acq_end_time = parser.isoparse(acq_info["end_time"])
            acquisition_duration = (acq_end_time - acq_start_time).total_seconds()

            # Last available timestamp
            last_timestamp = acquisition_duration

            if end_time == -1 or end_time > last_timestamp:
                end_time = last_timestamp

            if start_time > last_timestamp:
                return [], []

            byte_chest_index = 0
            raw_data_array_index = 0
            last_index = 0
            prev_timestamp = None
            nof_prev_timestamps = 0

            def __extract_data(start_time, end_time, nof_prev_timestamps):
                """
                Inner helper for data extraction from file chunks.

                Handles extraction based on start/end times and the number of
                previous timestamps. Preallocates buffers and reads chunks,
                validating counters and data integrity.

                Parameters:
                - start_time: float starting time in seconds
                - end_time: float ending time in seconds
                - nof_prev_timestamps: int number of previous timestamps to consider

                Returns:
                - tuple[np.ndarray, dict]: (raw_data_array, updated_comp_status)
                """
                # Preallocate byte_chest/raw_data_array with estimated sizes to
                # avoid repeated reallocation
                estimated_size = (nof_data_packet + 1) * (cmplt_pkt_size - data_protocol_size)
                byte_chest = np.empty(estimated_size, dtype="uint8")
                raw_data_array = np.empty(estimated_size, dtype="uint8")

                last_index = comp_status.get("last_index", 0)
                missing_bytes = comp_status.get("missing_bytes", 0)
                saved_bytes = comp_status.get("saved_bytes", 0)

                if last_index == 0 and start_idx != 0:
                    data_and_ts = nof_prev_timestamps * (dataframe_byte_size + timestamp_byte_size)
                    nof_counters = math.ceil(data_and_ts / data_packet_size)
                    packet_bytes = (nof_counters * data_protocol_size) + (
                        nof_prev_timestamps * (dataframe_byte_size + timestamp_byte_size)
                    )
                    missing_bytes = (
                        math.ceil(packet_bytes / cmplt_pkt_size) * cmplt_pkt_size - packet_bytes
                    )
                    log.debug(f"User customized time boudaries: {start_time}, {end_time}")
                    log.debug(f"- packet_bytes: {packet_bytes}")
                    log.debug(f"- missing_bytes: {missing_bytes}")
                    last_index = packet_bytes

                byte_chest_index = 0
                raw_data_array_index = 0
                data_byte_counter = 0
                extracted_data_length = 0
                end_time_flag = False
                skip_counter_check = False
                prev_timestamp = None

                with open(file_path, "rb") as f:
                    for n in range(nof_data_packet + 1):
                        file_index = last_index + (n * cmplt_pkt_size)
                        log.debug(f"missing_bytes: {missing_bytes}")
                        log.debug(f"file_index: {file_index}")
                        if file_index >= file_size:
                            comp_status["missing_bytes"] = byte_chest_index
                            comp_status["saved_bytes"] = raw_data_array_index
                            comp_status["last_index"] = file_index
                            # EOF - Not enough (cmplt_pkt_size) data to read!
                            # Extraction algorithm ends here
                            break
                        f.seek(file_index)
                        if last_index != 0:
                            if saved_bytes != 0 and saved_bytes <= missing_bytes:
                                raw_data = f.read(missing_bytes)
                                log.debug(f"Bytes read from file: {missing_bytes}")
                                comp_status["is_same_dps"] = True
                                if len(raw_data) < missing_bytes:
                                    return [], None
                                data_bytes = raw_data[:missing_bytes]
                                counter_bytes = []
                                skip_counter_check = True
                            else:
                                raw_data = f.read(missing_bytes + cmplt_pkt_size)
                                comp_status["is_same_dps"] = False
                                log.debug(f"Bytes read from file: {missing_bytes + cmplt_pkt_size}")
                                if len(raw_data) < missing_bytes + cmplt_pkt_size:
                                    return [], None
                                data_bytes = (
                                    raw_data[:missing_bytes]
                                    + raw_data[missing_bytes + data_protocol_size :]
                                )
                                counter_bytes = raw_data[
                                    missing_bytes : missing_bytes + data_protocol_size
                                ]
                            last_index += missing_bytes
                            comp_status["missing_bytes"] = missing_bytes = 0
                        else:
                            raw_data = f.read(cmplt_pkt_size)
                            log.debug(f"Bytes read from file: {cmplt_pkt_size}")
                            if (len(raw_data) + byte_chest_index) < cmplt_pkt_size:
                                return [], None

                            data_bytes = raw_data[data_protocol_size:]
                            counter_bytes = raw_data[:data_protocol_size]

                        if not skip_counter_check:
                            counter = struct.unpack("<I", counter_bytes)[0]
                            log.debug(f"Extracted counter: {counter}")
                            log.debug(f"data_byte_counter: {data_byte_counter}")
                            data_byte_counter = comp_status.get("prev_data_byte_counter")
                            if data_byte_counter is None:
                                comp_status["prev_data_byte_counter"] = counter
                            else:
                                if (counter - data_byte_counter) != data_packet_size:
                                    is_first_chunk = comp_status.get("is_first_chunk", True)
                                    if is_first_chunk and n == 0 and data_byte_counter == 0:
                                        # Drop the first complete packet (data_packet_size)
                                        # and continue with data extraction and validation
                                        log.warning(
                                            (
                                                "Counter mismatch at the beginning of the file: "
                                                f"{counter} != "
                                                f"{data_byte_counter + data_packet_size}"
                                            )
                                        )
                                        log.warning(
                                            (
                                                "Skipping the first packet and "
                                                "continuing with the data extraction"
                                            )
                                        )
                                        continue
                                    else:
                                        raise DataCorruptedException(file_path)
                                comp_status["prev_data_byte_counter"] = counter

                        # Directly copy data into preallocated array
                        data_bytes_length = len(data_bytes)
                        byte_chest[byte_chest_index : byte_chest_index + data_bytes_length] = (
                            np.frombuffer(data_bytes, dtype="uint8")
                        )
                        byte_chest_index += data_bytes_length

                        if timestamp_byte_size == 0:
                            read_start_bytes = sample_start * dataframe_byte_size
                            read_end_bytes = sample_end * dataframe_byte_size
                            if byte_chest_index >= read_end_bytes - read_start_bytes:
                                extracted_data_length = read_end_bytes - read_start_bytes
                                raw_data_array[:extracted_data_length] = byte_chest[
                                    :extracted_data_length
                                ]
                                raw_data_array_index += extracted_data_length
                                end_time_flag = True

                                is_same_data_packet = comp_status.get("is_same_dps")
                                if is_same_data_packet:
                                    bytes_processed = last_index
                                    comp_status["is_same_dps"] = False
                                else:
                                    bytes_processed = last_index + (n + 1) * cmplt_pkt_size

                                comp_status["missing_bytes"] = (
                                    byte_chest_index - extracted_data_length
                                )
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = (
                                    bytes_processed - comp_status["missing_bytes"]
                                )
                                byte_chest_index -= extracted_data_length
                                break
                        else:
                            extracted_timestamp = None
                            while byte_chest_index >= dataframe_byte_size + timestamp_byte_size:
                                extracted_timestamp_bytes = byte_chest[
                                    dataframe_byte_size : dataframe_byte_size + timestamp_byte_size
                                ]
                                extracted_timestamp = struct.unpack("d", extracted_timestamp_bytes)[
                                    0
                                ]
                                log.debug(f"start_time: {start_time}")
                                log.debug(f"extracted_timestamp: {extracted_timestamp}")
                                log.debug(f"end_time: {end_time}")
                                if extracted_timestamp > start_time:

                                    extracted_data_length = (
                                        dataframe_byte_size + timestamp_byte_size
                                    )
                                    raw_data_array[
                                        raw_data_array_index : raw_data_array_index
                                        + extracted_data_length
                                    ] = byte_chest[:extracted_data_length]
                                    raw_data_array_index += extracted_data_length

                                    if end_time != -1 and extracted_timestamp >= end_time:
                                        if prev_timestamp is None and comp_status.get(
                                            "is_first_chunk", False
                                        ):
                                            prev_timestamp = extracted_timestamp
                                            log.debug(f"prev_timestamp: {prev_timestamp}")
                                            if prev_timestamp > start_time:
                                                if last_index == 0:
                                                    prev_timestamp = comp_status.get("ioffset", 0)
                                                else:
                                                    end_time_flag = True
                                                    break

                                        end_time = extracted_timestamp
                                        end_time_flag = True

                                        is_same_data_packet = comp_status.get("is_same_dps")
                                        if is_same_data_packet:
                                            bytes_processed = last_index
                                            comp_status["is_same_dps"] = False
                                        else:
                                            bytes_processed = last_index + (n + 1) * cmplt_pkt_size

                                        comp_status["missing_bytes"] = (
                                            byte_chest_index - extracted_data_length
                                        )
                                        comp_status["saved_bytes"] = raw_data_array_index
                                        comp_status["last_index"] = (
                                            bytes_processed - comp_status["missing_bytes"]
                                        )
                                        break
                                    else:
                                        if prev_timestamp is None and comp_status.get(
                                            "is_first_chunk", False
                                        ):
                                            prev_timestamp = extracted_timestamp
                                            log.debug(f"prev_timestamp: {prev_timestamp}")
                                            if prev_timestamp > start_time:
                                                if last_index == 0:
                                                    prev_timestamp = comp_status.get("ioffset", 0)
                                                else:
                                                    end_time_flag = True
                                                    comp_status.pop("prev_data_byte_counter", None)
                                                    break

                                    byte_chest = byte_chest[extracted_data_length:]
                                    byte_chest_index -= extracted_data_length

                                else:
                                    is_first_chunk = comp_status.get("is_first_chunk", False)
                                    if is_first_chunk:
                                        prev_timestamp = extracted_timestamp
                                        log.debug(f"prev_timestamp: {prev_timestamp}")

                                    byte_chest = byte_chest[
                                        dataframe_byte_size + timestamp_byte_size :
                                    ]
                                    byte_chest_index -= dataframe_byte_size + timestamp_byte_size
                                    break

                            if (
                                "last_index" not in comp_status
                                and extracted_timestamp is not None
                                and (last_timestamp - extracted_timestamp)
                                < (s_samples_per_ts / odr)
                            ):
                                end_time_flag = True
                                bytes_processed = last_index + (n + 1) * cmplt_pkt_size
                                comp_status["missing_bytes"] = byte_chest_index
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = (
                                    bytes_processed - comp_status["missing_bytes"]
                                )
                                break

                            if (
                                last_index != 0
                                and extracted_timestamp is not None
                                and extracted_timestamp < end_time
                                and byte_chest_index != 0
                                and comp_status["missing_bytes"] != 0
                            ):
                                end_time_flag = True
                                comp_status["missing_bytes"] = byte_chest_index
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = (
                                    last_index - comp_status["missing_bytes"]
                                )
                                break

                        if end_time_flag:
                            break

                # Trim the preallocated arrays to the actual size of the data
                byte_chest = byte_chest[:byte_chest_index]
                raw_data_array = raw_data_array[:raw_data_array_index]
                return raw_data_array, prev_timestamp

            nof_prev_timestamps = max(0, nof_timestamps_in_start - 2)
            raw_data_array, prev_timestamp = __extract_data(
                start_time, end_time, nof_prev_timestamps
            )

            if nof_prev_timestamps != 0:
                while prev_timestamp is not None and prev_timestamp > start_time:
                    nof_prev_timestamps -= 1
                    raw_data_array, prev_timestamp = __extract_data(
                        start_time, end_time, nof_prev_timestamps
                    )

            log.debug("Data & Timestamp extraction algorithm COMPLETED!")
            data, timestamp = self.__process_datalog(
                comp_name,
                comp_status,
                raw_data_array,
                dataframe_byte_size,
                timestamp_byte_size,
                raw_flag,
                start_time,
                prev_timestamp,
            )

            if "last_index" not in comp_status:
                bytes_processed = last_index + (nof_data_packet + 1) * cmplt_pkt_size
                comp_status["missing_bytes"] = byte_chest_index
                comp_status["saved_bytes"] = raw_data_array_index
                comp_status["last_index"] = bytes_processed - comp_status["missing_bytes"]

            # DEBUG
            log.debug(f"data Len: {len(data)}")
            log.debug(f"Time Len: {len(timestamp)}")
            return data, timestamp

        elif c_type == ComponentTypeEnum.ACTUATOR.value and "odr" not in comp_status:
            if comp_name == MC_SLOW_TELEMETRY_COMP_NAME or comp_name == MC_FAST_TELEMETRY_COMP_NAME:
                if data_packet_size is not None:
                    with open(file_path, "rb") as f:
                        f_data = f.read()
                        if not f_data:
                            log.error(
                                (
                                    f'No data @ index: 0 for file "{file_path}" size: '
                                    f"{os.stat(f.name).st_size}[bytes]"
                                )
                            )
                            raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                        raw_data = np.fromstring(f_data, dtype="uint8")
                        new_array = self.remove_4bytes_every_n_optimized(raw_data, cmplt_pkt_size)

                    # NOTE: The following value should be obtained from:
                    # -SLOW MC TELEMETRIES: "n_of_enabled_slow_telemetries * data_type (bytes_size)"
                    # -FAST MC TELEMETRIES: "dim * data_type (bytes_size)"
                    data_packet_size = comp_status.get("usb_dps")
                    if data_packet_size is not None:
                        timestamp_byte_size = 8
                        dataframe_byte_size = data_packet_size - timestamp_byte_size

                    data, timestamp = self.__process_datalog(
                        comp_name,
                        comp_status,
                        new_array,
                        dataframe_byte_size,
                        timestamp_byte_size,
                        raw_flag=raw_flag,
                    )

                    # DEBUG
                    log.debug(f"data Len: {len(data)}")
                    log.debug(f"Time Len: {len(timestamp)}")
                    return data, timestamp
                else:
                    log.error("Actuator type not supported")
                    return None, None

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:  # "fft":
                log.debug("FFT Algorithm! No batch")

                dataframe_byte_size = int(s_dim * s_data_type_len)
                timestamp_byte_size = 0

                with open(file_path, "rb") as f:
                    f_data = f.read()
                    if not f_data:
                        log.error(
                            (
                                f'No data @ index: 0 for file "{file_path}" size: '
                                f"{os.stat(f.name).st_size}[bytes]"
                            )
                        )
                        raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                    raw_data = np.fromstring(f_data, dtype="uint8")
                    new_array = self.remove_4bytes_every_n_optimized(raw_data, cmplt_pkt_size)

                data, timestamp = self.__process_datalog(
                    comp_name,
                    comp_status,
                    new_array,
                    dataframe_byte_size,
                    timestamp_byte_size,
                    raw_flag=raw_flag,
                    start_time=start_time,
                )

                # DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")
                return data, timestamp
            else:
                log.error("Algorithm type not supported")
                return None, None

    def get_data_and_timestamps(
        self, sensor_name, sub_sensor_type, start_time=0, end_time=-1, raw_flag=False
    ):
        """
        Deprecated: Parse a sensor .dat file into data and timestamps.

        This method is deprecated and will be removed in a future release.
        Use get_data_and_timestamps_batch(comp_name, comp_status, start_time,
        end_time, raw_flag) instead.
        Note: This method does not handle interface packetization. It reads
        the entire .dat file at once, which may lead to high memory usage for large files.
        """
        warnings.warn(
            "get_data_and_timestamps is deprecated. "
            "Use get_data_and_timestamps_batch(comp_name, comp_status, start_time, "
            "end_time, raw_flag) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _ = sub_sensor_type  # currently not used
        # get sensor component status
        s_stat = self.__get_sensor_status(sensor_name)

        # get acquisition interface
        interface = self.acq_info_model["interface"]

        # data protocol size:
        data_protocol_size = 4

        data_packet_size = 0
        # data packet size (0:sd card, 1:usb, 2:ble, 3:serial)
        if interface == 0:
            data_packet_size = s_stat["sd_dps"] - data_protocol_size
        elif interface == 1:
            data_packet_size = s_stat["usb_dps"]
        elif interface == 2:
            data_packet_size = s_stat["ble_dps"]
        elif interface == 3:
            data_packet_size = s_stat["serial_dps"]
        else:
            log.error(f"Unknown interface: {interface}. check your device_config.json file")
            raise

        # get dat file path and size (obtained from "sensor_name + sub_sensor_type")
        file_path = self.__get_sensor_file_path(sensor_name)
        file_size = os.path.getsize(file_path)

        cmplt_pkt_size = data_packet_size + data_protocol_size
        nof_data_packet = file_size // cmplt_pkt_size  # "//" math.floor equivalent
        checked_file_path = os.path.splitext(os.path.abspath(file_path))[0] + "_checked.dat"

        with open(checked_file_path, "wb") as f, open(file_path, "rb") as rf:
            # cmplt_pkt_size = data_packet_size + data_protocol_size
            for n in range(nof_data_packet):
                index = n * cmplt_pkt_size
                rf.seek(index)
                rf_data = rf.read(cmplt_pkt_size)[4:]
                if not rf_data:
                    log.error(
                        (
                            f'No data @ index: {index} for file "{file_path}" size: '
                            f"{os.stat(f.name).st_size}[bytes]"
                        )
                    )
                    raise NoDataAtIndexError(index, file_path, os.stat(f.name).st_size)
                f.write(np.frombuffer(rf_data, dtype="uint8"))

        # get checked dat file path and size (obtained from "sensor_name + sub_sensor_type")
        # reusing the same variables
        file_path = self.__get_checked_sensor_file_path(sensor_name)
        file_size = os.path.getsize(file_path)

        c_type = s_stat.get("c_type")

        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = s_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                # get FFT algo "dimensions" --> FFT Length
                s_dim = s_stat.get("fft_length")
            else:
                s_dim = s_stat.get("dim")
        else:
            # get sensor dimensions
            s_dim = s_stat.get("dim", 1)

        # get Data type byte length
        s_data_type_len = TypeConversion.check_type_length(s_stat["data_type"])

        # get samples per ts
        spts = s_stat.get("samples_per_ts", {})
        if isinstance(spts, int):
            s_samples_per_ts = spts
        else:
            s_samples_per_ts = spts.get("val", 0)

        if c_type == ComponentTypeEnum.SENSOR.value:
            # NOTE sample_end = N/s = 26667 in 1 sec, 266670 in 10 sec
            # --> 26667*10 in 1*10 sec --> ODR*end_time (sec) = sample_end
            # NOTE sample_start = N/s = 104 in 1 sec, 1040 in 10 sec
            # --> 104*10 in 1*10 sec --> ODR*start_time (sec) = sample_start
            s_category = s_stat.get("sensor_category")
            if (
                s_category is not None
                and s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value
            ):
                odr = (
                    1 / (s_stat.get("intermeasurement_time") / 1000)
                    if s_stat.get("intermeasurement_time") > s_stat.get("exposure_time") / 1000 + 6
                    else (1 / ((s_stat.get("exposure_time") / 1000 + 6) / 1000))
                )
            elif (
                s_category is not None
                and s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value
            ):
                odr = 1 / (s_stat.get("adc_conversion_time") / 1000000)
            else:
                odr = s_stat.get("measodr")
                if odr is None or odr == 0:
                    odr = s_stat.get("odr", 1)
            sample_end = int(odr * end_time) if end_time != -1 else -1
            total_samples = file_size // (s_data_type_len * s_dim)
            if sample_end > total_samples:
                sample_end = total_samples
            sample_start = int(odr * start_time)

            # SAMPLES_PER_TS check
            if s_samples_per_ts > total_samples:
                s_samples_per_ts = 0
            try:
                # Sample per Ts == 0
                if s_samples_per_ts == 0:
                    if sample_end == -1:
                        sample_end = total_samples

                    read_start_bytes = sample_start * (s_dim * s_data_type_len)
                    read_end_bytes = sample_end * (s_dim * s_data_type_len)  # dataframe_byte_size

                    dataframe_byte_size = read_end_bytes - read_start_bytes
                    timestamp_byte_size = 0
                    blocks_before_ss = 0

                # Sample per Ts != 0
                else:
                    dataframe_byte_size = s_samples_per_ts * s_dim * s_data_type_len
                    timestamp_byte_size = 8

                    if sample_end == -1:
                        n_of_blocks_in_file = file_size // (
                            timestamp_byte_size + dataframe_byte_size
                        )
                        sample_end = n_of_blocks_in_file * s_samples_per_ts

                    blocks_before_ss = sample_start // s_samples_per_ts
                    blocks_before_se = sample_end // s_samples_per_ts

                    read_start_bytes = (
                        (blocks_before_ss * dataframe_byte_size)
                        + ((blocks_before_ss - 1) * timestamp_byte_size)
                        if blocks_before_ss > 0
                        else 0
                    )
                    read_end_bytes = ((blocks_before_se + 1) * dataframe_byte_size) + (
                        (blocks_before_se + 1) * timestamp_byte_size
                    )

                with open(file_path, "rb") as f:
                    f.seek(read_start_bytes)
                    raw_data = f.read(read_end_bytes - read_start_bytes)
                    if len(raw_data) == 0:
                        log.error(
                            (
                                f'No data @ index: {read_start_bytes} for file "{file_size}" size: '
                                f"{os.stat(f.name).st_size}[bytes]"
                            )
                        )
                        raise NoDataAtIndexError(
                            read_start_bytes, file_path, os.stat(f.name).st_size
                        )

                raw_data = np.fromstring(raw_data, dtype="uint8")

                # if the start_sample isn't in the first block (pre_t_bytes_id != 0)
                if read_start_bytes != 0:
                    first_timestamp = raw_data[:timestamp_byte_size] if s_samples_per_ts != 0 else 0
                    s_stat["ioffset"] = (
                        np.frombuffer(first_timestamp, dtype="double")
                        if s_samples_per_ts != 0
                        else 0
                    )
                    # remove the first timestamp
                    raw_data = raw_data[timestamp_byte_size:]

                data, timestamp = self.__process_datalog(
                    sensor_name,
                    s_stat,
                    raw_data,
                    dataframe_byte_size,
                    timestamp_byte_size,
                    raw_flag,
                    start_time,
                )

                # DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")

                os.remove(file_path)

                return data, timestamp

            except MemoryError:
                log.error(f"Memory Error occoured! You should batch process your {file_path} file")
                os.remove(file_path)
                raise
            except OverflowError:
                log.error(f"Memory Error occoured! You should batch process your {file_path} file")
                os.remove(file_path)
                raise

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                log.info("FFT Algorithm!")

                dataframe_byte_size = int(s_dim * s_data_type_len)
                timestamp_byte_size = 0

                with open(file_path, "rb") as f:
                    f_data = f.read()
                    if not f_data:
                        log.error(
                            (
                                f'No data @ index: {0} for file "{file_path}" size: '
                                f"{os.stat(f.name).st_size}[bytes]"
                            )
                        )
                        raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                    raw_data = np.fromstring(f_data, dtype="uint8")

                data, timestamp = self.__process_datalog(
                    sensor_name,
                    s_stat,
                    raw_data,
                    dataframe_byte_size,
                    timestamp_byte_size,
                    raw_flag=raw_flag,
                )

                # DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")
                os.remove(file_path)
                return data, timestamp
            log.error("Algorithm type not supported")
            os.remove(file_path)
            return None, None

        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            if sensor_name in (MC_SLOW_TELEMETRY_COMP_NAME, MC_FAST_TELEMETRY_COMP_NAME):
                if data_packet_size is not None:
                    timestamp_byte_size = 8
                    dataframe_byte_size = data_packet_size - timestamp_byte_size
                    n_of_samples = int(file_size / dataframe_byte_size)
                    with open(file_path, "rb") as f:
                        f_data = f.read()
                        if not f_data:
                            log.error(
                                (
                                    f'No data @ index: {0} for file "{file_path}" size: '
                                    f"{os.stat(f.name).st_size}[bytes]"
                                )
                            )
                            raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                        raw_data = np.fromstring(f_data, dtype="uint8")
                    if n_of_samples >= 1:
                        first_timestamp = raw_data[
                            dataframe_byte_size : dataframe_byte_size + timestamp_byte_size
                        ]

                    data, timestamp = self.__process_datalog(
                        sensor_name,
                        s_stat,
                        raw_data,
                        dataframe_byte_size,
                        timestamp_byte_size,
                        raw_flag=raw_flag,
                    )
                    # DEBUG
                    log.debug(f"data Len: {data}")
                    log.debug(f"Time Len: {timestamp}")
                    os.remove(file_path)
                    return data, timestamp
                log.error("Actuator type not supported")
                os.remove(file_path)
                return None, None

    def get_mlc_ispu_output_column_names(self, comp_name):
        """Get ISPU output column names from the ISPU output format."""
        if "_ispu" in comp_name or "_mlc" in comp_name:
            if comp_name in list(self.mlc_ispu_output_formats.keys()):
                outputs = self.mlc_ispu_output_formats[comp_name].get("outputs")
                core = self.mlc_ispu_output_formats[comp_name].get("core")
                if outputs:
                    return [o["name"] for o in outputs]
                else:
                    if core == "MLC":
                        return [f"MLC_output_{i}" for i in range(8)]
                    elif core == "ISPU":
                        log.error(f"ISPU output format for {comp_name} is missing 'outputs' key")
                        return None
            else:
                if "_mlc" in comp_name:
                    return [f"MLC_output_{i}" for i in range(8)]
                log.error(f"Component name {comp_name} not found in MLC/ISPU output formats read from the acquisition folder")
                return None
        else:
            log.error(f"Component name {comp_name} does not indicate an ISPU or MLC component")
            return None

    # def get_ispu_output_types(self):
    #     """Get ISPU output types from the ISPU output format."""
    #     if self.ispu_output_format is not None:
    #         return [TypeConversion.check_type(o["type"]) for o in self.ispu_output_format["output"]]
    #     else:
    #         return None

    def get_mlc_ispu_output_types(self, comp_name):
        """Get ISPU output types from the ISPU output format."""
        if "_ispu" in comp_name or "_mlc" in comp_name:
            if comp_name in list(self.mlc_ispu_output_formats.keys()):
                outputs = self.mlc_ispu_output_formats[comp_name].get("outputs")
                core = self.mlc_ispu_output_formats[comp_name].get("core")
                if outputs:
                    return [TypeConversion.check_type(o["type"]) for o in outputs]
                else:
                    if core == "MLC":
                        return [TypeConversion.check_type("int8")] * 8
                    elif core == "ISPU":
                        log.error(f"ISPU output format for {comp_name} is missing 'outputs' key")
                        return None
            else:
                if "_mlc" in comp_name:
                    return [TypeConversion.check_type("int8")] * 8
                log.error(f"Component name {comp_name} not found in MLC/ISPU output formats read from the acquisition folder")
                return None
        else:
            log.error(f"Component name {comp_name} does not indicate an ISPU or MLC component")
            return None

    def __get_mems_columns_names(self, ss_stat, sensor_name, s_type, num_axes):
        """Get MEMS sensor output column names.
        Parameters:
        - ss_stat (dict): Sub-sensor status dictionary.
        - sensor_name (str): Name of the sensor.
        - s_type (str): Sensor type.
        - num_axes (int): Number of axes of the sensor.

        Returns:
        - list: List of column names.

        Raises:
        - NSensorAxesError: If the number of sensor axes is invalid.
        """
        if not (s_type == "mlc" or s_type == "stredl" or s_type == "ispu"):
            cc = ["x", "y", "z"] if num_axes == 3 else ["x", "y"] if num_axes == 2 else []
            col_prefix = s_type[0].upper() + "_" if cc else ""
            col_postfix = ""
            if "unit" in ss_stat:
                unit = ss_stat["unit"]
                col_postfix = " [" + UnitMap().unit_dict.get(unit, unit) + "]"
            c = [col_prefix + s + col_postfix for s in cc] if cc else [s_type.upper() + col_postfix]
        else:
            c = self.get_mlc_ispu_output_column_names(comp_name=sensor_name)
            # if s_type == "ispu":
            #     c = self.get_mlc_ispu_output_column_names(ispu_comp_name=sensor_name)
            # else:
            #     if num_axes > 0:
            #         cc = range(ss_stat["dim"])
            #         col_prefix = s_type[0].upper() + "_"
            #         c = [col_prefix + str(s) for s in cc]
            #     else:
            #         log.error(f"Wrong number of sensor axes ({num_axes})")
            #         raise NSensorAxesError(sensor_name)
        return c

    # Function to find the nearest index for a given time in the times array
    def find_nearest_index(self, array, value):
        """
        Find the index of the nearest value in an array.

        Parameters:
        - array (np.ndarray): Input array.
        - value (float): Value to find the nearest index for.

        Returns:
        - int: Index of the nearest value.
        """
        array = np.squeeze(array)  # Remove single-dimensional entries
        idx = (np.abs(array - value)).argmin()
        return idx

    def get_sensor_axis_label(self, ss_stat, sensor_name):
        """
        Get sensor axis labels based on the sensor category and type.
        Parameters:
        - ss_stat (dict): Sub-sensor status dictionary.
        - sensor_name (str): Name of the sensor.
        Returns:
        - list: List of axis labels.
        """
        s_type = ""
        c_type = ss_stat.get("c_type")
        if c_type == ComponentTypeEnum.SENSOR.value:
            num_axes = int(ss_stat.get("dim", 1))
            _, s_type = FileManager.decode_file_name(sensor_name)
            s_category = ss_stat.get("sensor_category")
            if s_category is not None:
                al = []
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    al = ["Red", "Visible", "Blue", "Green", "IR", "Clear"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                    al = [
                        "Tambient (raw)",
                        "Tobject (raw)",
                        "Tobject (emb_comp)",
                        "Tpresence",
                        "Presence flag",
                        "Tmotion",
                        "Motion flag",
                        "Tobject (sw_comp)",
                        "Tobject_change (sw_comp)",
                        "Motion flag (sw_comp)",
                        "Presence flag (sw_comp)",
                    ]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                    if ss_stat.get("output_format"):
                        resolution = ss_stat.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                        else:
                            return []
                        for i in range(res * res):
                            al += [f"Target Status Z{i}", f"Distance Z{i}"]
                    else:  # NOTE: Code for old firmware versions
                        res = 4 if ss_stat["dim"] == 128 else 8
                        for i in range(res * res):
                            al += [
                                f"N Target Z{i}",
                                f"Ambient per SPAD Z{i}",
                                f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",
                                f"Distance Z{i}",
                                f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",
                                f"Distance Z{i}",
                            ]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA:
                    raise UnsupportedSensorCategoryError(sensor_name)
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    al = ["Voltage [mV]", "Voltage(VShunt) [mV]", "Current [A]", "Power [mW]"]
                else:
                    if not (s_type == "mlc" or s_type == "stredl" or s_type == "ispu"):
                        al = (
                            ["x", "y", "z"]
                            if num_axes == 3
                            else ["x", "y"] if num_axes == 2 else ["x"]
                        )
                    else:
                        al = self.get_mlc_ispu_output_column_names(comp_name=sensor_name)
                        # if s_type == "ispu":
                        #     al = self.get_mlc_ispu_output_column_names()
                        # else:
                        #     if num_axes > 0:
                        #         cc = range(ss_stat["dim"])
                        #         col_prefix = "reg_"
                        #         al = [col_prefix + str(s) for s in cc]
                return al
            else:
                cc = range(ss_stat["dim"])
                col_prefix = "reg_"
                return [col_prefix + str(s) for s in cc]
        else:
            return None

    def get_component_columns_names(self, ss_stat, sensor_name):
        """
        Get component output column names based on the component type.

        Parameters:
        - ss_stat (dict): Sub-sensor status dictionary.
        - sensor_name (str): Name of the sensor.

        Returns:
        - list: List of column names.
        """
        s_type = ""
        # d_type = ss_stat.get("data_type")
        c_type = ss_stat.get("c_type")
        if c_type == ComponentTypeEnum.SENSOR.value:
            num_axes = int(ss_stat.get("dim", 1))
            _, s_type = FileManager.decode_file_name(sensor_name)
            s_category = ss_stat.get("sensor_category")
            if s_category is not None:
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    c = ["Red", "Visible", "Blue", "Green", "IR", "Clear"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                    c = [
                        "Tambient (raw)",
                        "Tobject (raw)",
                        "Tobject (emb_comp)",
                        "Tpresence",
                        "Presence flag",
                        "Tmotion",
                        "Motion flag",
                        "Tobject (sw_comp)",
                        "Tobject_change (sw_comp)",
                        "Motion flag (sw_comp)",
                        "Presence flag (sw_comp)",
                    ]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                    if ss_stat.get("output_format"):
                        resolution = ss_stat.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                        else:
                            return []
                        c = []
                        # for i in range(res):
                        #     for j in range(res):
                        #         c += [f"Target Status T1_Z({i},{j})",f"Distance T1_Z({i},{j})"]
                        for i in range(res * res):
                            c += [f"Target Status Z{i}", f"Distance Z{i}"]
                    else:  # NOTE: Code for old firmware versions
                        res = 4 if ss_stat["dim"] == 128 else 8
                        c = []
                        # Old firmware example (columns omitted for brevity):
                        # N Target Z(i,j), Ambient per SPAD Z(i,j), Signal per SPAD T1_Z(i,j),
                        # Target Status T1_Z(i,j), Distance T1_Z(i,j), Signal per SPAD T2_Z(i,j),
                        # Target Status T2_Z(i,j), Distance T2_Z(i,j)
                        for i in range(res * res):
                            c += [
                                f"N Target Z{i}",
                                f"Ambient per SPAD Z{i}",
                                f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",
                                f"Distance Z{i}",
                                f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",
                                f"Distance Z{i}",
                            ]

                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA:
                    raise UnsupportedSensorCategoryError(sensor_name)
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    c = ["Voltage [mV]", "Voltage(VShunt) [mV]", "Current [A]", "Power [mW]"]
                else:
                    c = self.__get_mems_columns_names(ss_stat, sensor_name, s_type, num_axes)
            else:
                c = self.__get_mems_columns_names(ss_stat, sensor_name, s_type, num_axes)

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = ss_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                cc = range(ss_stat["fft_length"])
            else:
                cc = range(ss_stat["dim"])
            col_prefix = sensor_name.upper() + "_"
            c = [col_prefix + str(s) for s in cc]

        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            telemetries_keys = self.__get_active_mc_telemetries_names(ss_stat, sensor_name)
            cc = range(len(telemetries_keys))
            c = [tk.upper() for tk in telemetries_keys]

        return c

    def __to_dataframe(
        self, data, time, ss_stat, sensor_name, labeled=False, which_tags: list = [], raw_flag=False
    ):
        """Assemble a labeled DataFrame from decoded arrays.

        Parameters:
        - data: np.ndarray data samples (2D or 1D depending on category)
        - time: np.ndarray timestamps in seconds
        - ss_stat: dict component status
        - sensor_name: str component name
        - labeled: bool add tag boolean columns for ranges
        - which_tags: list[str] specific tag labels to include
        - raw_flag: bool keep raw types and skip sensitivity scaling

        Returns:
        - pandas.DataFrame with rounding applied to float columns and proper dtypes.
        """
        if data is not None and time is not None:
            cols = []
            s_type = ""
            c_type = ss_stat.get("c_type")
            if c_type == ComponentTypeEnum.SENSOR.value:
                _, s_type = FileManager.decode_file_name(sensor_name)

            if s_type != "ispu" and s_type != "mlc" and s_type != "stredl":
                try:
                    if len(time) > len(data):
                        time = time[: len(data)]
                    val = np.concatenate((time, data), axis=1)
                except Exception:
                    pass
            else:
                mlc_ispu_out_types = self.get_mlc_ispu_output_types(comp_name=sensor_name)
                if mlc_ispu_out_types is not None:
                    output_bytes_count = sum(
                        TypeConversion.check_type_length(ot) for ot in mlc_ispu_out_types
                    )
                    unpack_str = "=" + "".join(
                        TypeConversion.get_format_char(ot) for ot in mlc_ispu_out_types
                    )
                    output_data = []
                    output_time = []
                    for d, t in zip(data, time):
                        row_bytes = np.asarray(d, dtype=np.uint8).tobytes()
                        if len(row_bytes) < output_bytes_count:
                            continue
                        output_data.append(struct.unpack(unpack_str, row_bytes[:output_bytes_count]))
                        output_time.append(t)

                    if len(output_data) == 0:
                        raise DataExtractionError(sensor_name)

                    np_output_data = np.array(output_data)
                    val = np.array(output_time).reshape(-1, 1)
                    val = np.append(val, np_output_data, axis=1)
                else:
                    raise MissingISPUOutputDescriptorException(sensor_name)
                # ispu_out_types = self.get_ispu_output_types()
                # if ispu_out_types is not None:
                #     output_bytes_count = sum(
                #         TypeConversion.check_type_length(ot) for ot in ispu_out_types
                #     )
                #     unpack_str = "=" + "".join(
                #         TypeConversion.get_format_char(ot) for ot in ispu_out_types
                #     )
                #     output_data = [
                #         struct.unpack(unpack_str, np.array(d[0:output_bytes_count])) for d in data
                #     ]
                #     np_output_data = np.array(output_data)
                #     final_out_data = [np_output_data[:, i] for i in range(len(ispu_out_types))]
                #     final_out_data = np.transpose(final_out_data)
                #     val = np.array(time)
                #     val = np.append(val, final_out_data, axis=1)
                # else:
                #     raise MissingISPUOutputDescriptorException(sensor_name)

            cols = np.concatenate(
                (["Time"], self.get_component_columns_names(ss_stat, sensor_name)), axis=0
            )

            try:
                ss_data_frame = pd.DataFrame(data=val, columns=cols)
            except Exception:
                pass

            if labeled:
                tags = self.get_tags()
                if len(tags) == 0:
                    raise MissingTagsException()
                if len(which_tags) > 0:
                    filtered_tags = [t for t in tags if t["label"] in which_tags]
                    tags = filtered_tags

                for tag in tags:
                    tag_label = tag.get("label")
                    tag_times = tag.get("times")
                    for t in tag_times:
                        enter_time = t[0]
                        exit_time = t[1]
                        # Find the nearest indices for the enter and exit times
                        enter_index = self.find_nearest_index(time, enter_time)
                        exit_index = self.find_nearest_index(time, exit_time)

                        # Create an array of booleans with the same length as times_array
                        bool_array = np.zeros_like(time, dtype=bool)
                        if enter_time <= time[-1]:
                            if not (exit_time <= time[-1] and (enter_index == exit_index)):
                                # Set True for indices in [enter_index, exit_index] (inclusive)
                                bool_array[enter_index : exit_index + 1] = True

                        # Flatten the boolean array to match the shape of the input times array
                        bool_array = bool_array.flatten()

                        if tag_label not in ss_data_frame:
                            ss_data_frame[tag_label] = bool_array
                        else:
                            ss_data_frame[tag_label] = ss_data_frame[tag_label] | bool_array

            sensitivity = ss_stat.get("sensitivity", 1)
            if c_type == ComponentTypeEnum.ACTUATOR.value:
                sensitivity = 0
            # Round time once
            ss_data_frame["Time"] = ss_data_frame["Time"].round(6)

            data_cols = list(cols[1:])  # all data columns (skip "Time")

            if s_type != "ispu":
                data_type = ss_stat.get("data_type")
                # Cast data columns when raw or unscaled
                if (raw_flag or sensitivity == 1) and data_type:
                    target_dtype = TypeConversion.get_np_dtype(data_type)
                    ss_data_frame.loc[:, data_cols] = ss_data_frame.loc[:, data_cols].astype(
                        target_dtype, errors="ignore"
                    )
            else:
                # ISPU: column-wise casting based on descriptor types
                data_types = mlc_ispu_out_types
                if (raw_flag or sensitivity == 1) and data_types:
                    dtype_map = {
                        col: TypeConversion.get_np_dtype(data_types[i])
                        for i, col in enumerate(data_cols)
                    }
                    ss_data_frame = ss_data_frame.astype(dtype_map, errors="ignore")

            # Round only float columns to 6 decimals
            float_cols = ss_data_frame.loc[:, data_cols].select_dtypes(
                include=["float", "float32", "float64", "double"]
            ).columns
            if len(float_cols) > 0:
                ss_data_frame.loc[:, float_cols] = ss_data_frame.loc[:, float_cols].round(6)

            return ss_data_frame
        log.error(f"Error extracting data and timestamp from sensor {sensor_name}")
        raise DataExtractionError(sensor_name)

    def get_dataframe(
        self,
        sensor_name,
        sensor_type=None,
        start_time=0,
        end_time=-1,
        labeled=False,
        raw_flag=False,
    ):
        """
        Deprecated: Parse a sensor .dat file into a labeled DataFrame.

        This method is deprecated and will be removed in a future release.
        Use get_dataframe_batch(comp_name, comp_status, start_time, end_time,
        labeled, raw_flag, which_tags) instead.
        """
        warnings.warn(
            "get_dataframe is deprecated. "
            "Use get_dataframe_batch(comp_name, comp_status, start_time, end_time, "
            "labeled, raw_flag, which_tags) instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        # get sensor component status
        s_stat = self.__get_sensor_status(sensor_name)

        res = self.get_data_and_timestamps(sensor_name, sensor_type, start_time, end_time, raw_flag)
        if res is not None:
            data, time = res
            # data, time, ss_stat, sensor_name, labeled=False,
            # which_tags: list = [], raw_flag=False
            return self.__to_dataframe(data, time, s_stat, sensor_name, labeled, [], raw_flag)
        log.error(f"Error extracting data and timestamps from {sensor_name} sensor .dat file")
        raise DataExtractionError(sensor_name)

    def get_dataframe_batch(
        self,
        comp_name,
        comp_status,
        start_time=0,
        end_time=-1,
        labeled=False,
        raw_flag=False,
        which_tags: list = [],
    ):
        """Return a pandas DataFrame for a component using batch parsing.

        Columns:
        - 'Time' first column.
        - Component-specific data columns with units and labels.
        - Optional boolean tag columns when labeled=True and which_tags provided.

        Returns:
        - pandas.DataFrame or None if no data in the window.
        """
        res = self.get_data_and_timestamps_batch(
            comp_name, comp_status, start_time, end_time, raw_flag
        )
        if res[0] is not None and res[1] is not None:
            data, time = res
            if len(data) > 0:
                return self.__to_dataframe(
                    data, time, comp_status, comp_name, labeled, which_tags, raw_flag
                )
        return None

    def get_dat_file_list(self):
        """
        Retrieves a list of .dat files from the acquisition folder associated with this HSDatalog
        instance. The method uses the FileManager class to search for .dat files within the
        acquisition folder path.

        :return: A list of .dat file paths.
        """
        # Use the FileManager class to get a list of .dat files from the acquisition folder.
        # The '__acq_folder_path' is an instance attribute that stores the path to the
        # acquisition folder.
        return FileManager.get_dat_files_from_folder(self.__acq_folder_path)

    # === OFFLINE Plots ===

    # Plotting-related helpers and public methods below
    # Each plotting method documents inputs/behavior and returns Figure(s) or serves Dash.
    # Plots Helper Functions
    def __plot_ranging_sensor(self, sensor_name, ss_data_frame, res, output_format):
        """Build animated ToF heatmaps and pixel-over-time charts.

        Parameters:
        - sensor_name: str
        - ss_data_frame: DataFrame with Time + flattened target status/distance columns
        - res: int resolution (e.g., 8 for 8x8)
        - output_format: dict describing output fields and stride

        Returns:
        - list[plotly.graph_objects.Figure]
        """

        # Function to extract the target identifier from the key
        def __extract_target_identifier(key):
            if key != "nof_outputs":
                return "".join(key.split("_")[:-1])
            return None

        # Group the targets
        targets = {}
        new_shape = (res, res)

        # Create a list to store figures to be returned
        figures = []

        nof_outputs = output_format.get("nof_outputs")
        for key, value in output_format.items():
            identifier = __extract_target_identifier(key)
            if identifier is not None:
                # Initialize the target group if it doesn't exist
                if identifier not in targets:
                    targets[identifier] = {"status": None, "distance": None}
                # Assign the status or distance to the target group
                if "status" in key:
                    targets[identifier]["status"] = value
                elif "distance" in key:
                    targets[identifier]["distance"] = value

        for t in targets:
            dist_id = targets[t]["distance"]["start_id"] + 1
            dist_df = ss_data_frame.iloc[:, range(dist_id, len(ss_data_frame.columns), nof_outputs)]
            status_id = targets[t]["status"]["start_id"] + 1
            status_df = ss_data_frame.iloc[
                :, range(status_id, len(ss_data_frame.columns), nof_outputs)
            ]
            status_df.columns = dist_df.columns
            mask_df = status_df == 5
            times = ss_data_frame["Time"]
            # use the boolean matrix as a mask for the second dataframe
            masked_df = dist_df.where(mask_df)
            nof_rows = len(mask_df)

            dist_matrices = np.empty((nof_rows,), dtype=object)
            for r_id in range(nof_rows):
                row_t1 = masked_df.iloc[r_id]
                t1_mat = np.array(row_t1.values).reshape(new_shape).astype("float")
                t1_mat = np.rot90(t1_mat, k=3)
                t1_mat = np.flip(t1_mat, axis=0)
                t1_mat = np.swapaxes(t1_mat, 0, 1)
                dist_matrices[r_id] = t1_mat

            # Handle NaN values by filling them with a default value (e.g., 0)
            dist_matrices = [np.nan_to_num(matrix, nan=-1) for matrix in dist_matrices]

            # Prepare annotation arrays for each frame
            annotations_list = []
            for frame_idx in range(nof_rows):
                frame_annotations = np.zeros((new_shape[0], new_shape[1]), dtype=object)
                for i in range(new_shape[0]):
                    for j in range(new_shape[1]):
                        z_index = new_shape[0] * (new_shape[0] - 1 - i) + (new_shape[1] - 1 - j)
                        z_value = dist_matrices[frame_idx][i][j]
                        if z_value == -1:
                            z_str = "-"
                        else:
                            z_str = f"{int(z_value)}"
                        frame_annotations[i][j] = f"Z{z_index}<br>{z_str}"
                annotations_list.append(frame_annotations)

            # Define a custom colorscale
            custom_colorscale = [
                [0, "red"],  # Map -1 to red
                [0.001, "rgb(68, 1, 84)"],  # Start of Viridis colorscale
                [0.125, "rgb(72, 35, 116)"],
                [0.25, "rgb(64, 67, 135)"],
                [0.375, "rgb(52, 94, 141)"],
                [0.5, "rgb(41, 120, 142)"],
                [0.625, "rgb(32, 144, 140)"],
                [0.75, "rgb(34, 167, 132)"],
                [0.875, "rgb(68, 190, 112)"],
                [1, "rgb(253, 231, 37)"],  # End of Viridis colorscale
            ]

            # Create frames for animation
            frames = [
                go.Frame(
                    data=[
                        go.Heatmap(
                            z=dist_matrices[i],
                            colorscale=custom_colorscale,
                            zmin=-1,
                            zmax=4000,
                            text=annotations_list[i],
                            texttemplate="%{text}",
                        )
                    ],
                    name=str(i),
                )
                for i in range(nof_rows)
            ]

            # Create the initial heatmap with the first frame's annotations
            fig = go.Figure(
                data=[
                    go.Heatmap(
                        z=dist_matrices[0],
                        colorscale=custom_colorscale,
                        zmin=-1,
                        zmax=4000,
                        text=annotations_list[0],
                        texttemplate="%{text}",
                    )
                ],
                frames=frames,
                layout=go.Layout(
                    title=f"{sensor_name.upper()} - Target {t}",
                    xaxis=dict(scaleanchor="y", scaleratio=1),
                    yaxis=dict(scaleratio=1),
                    updatemenus=[
                        {
                            "type": "buttons",
                            "direction": "right",
                            "buttons": [
                                {
                                    "label": "Play",
                                    "method": "animate",
                                    "args": [
                                        None,
                                        {
                                            "frame": {"duration": 500, "redraw": True},
                                            "fromcurrent": True,
                                        },
                                    ],
                                },
                                {
                                    "label": "Pause",
                                    "method": "animate",
                                    "args": [
                                        [None],
                                        {
                                            "frame": {"duration": 0, "redraw": True},
                                            "mode": "immediate",
                                        },
                                    ],
                                },
                            ],
                            "showactive": True,
                            "x": 1,
                            "y": -0.15,
                            "xanchor": "right",
                            "yanchor": "bottom",
                        }
                    ],
                    sliders=[
                        {
                            "steps": [
                                {
                                    "args": [
                                        [str(i)],
                                        {
                                            "frame": {"duration": 500, "redraw": True},
                                            "mode": "immediate",
                                        },
                                    ],
                                    "label": f"{times[i]:.2f} s",
                                    "method": "animate",
                                }
                                for i in range(nof_rows)
                            ],
                            "transition": {"duration": 300},
                            "x": 0,
                            "y": -0.1,
                            "currentvalue": {"prefix": "Time: "},
                            "len": 1,
                        }
                    ],
                ),
            )

            # Add legend for invalid zones
            fig.add_trace(
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    marker=dict(size=10, color="red", symbol="square"),
                    legendgroup="Invalid Zones",
                    showlegend=True,
                    orientation="h",
                    name="Invalid Zones",
                )
            )

            # Update layout to place the legend above the graph and make it unclickable
            fig.update_layout(
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1,
                    itemclick=False,  # Disable click events
                    itemdoubleclick=False,  # Disable double-click events
                )
            )

            # Change the browser tab name using the sensor_name
            fig.update_layout(title_text=f"{sensor_name.upper()}", title_x=0.5)

            # Add the figure to the list of figures
            figures.append(fig)

        figures.append(
            self.__plot_pixels_over_time(sensor_name, ss_data_frame, res * res, masked_df)
        )

        return figures

    def __plot_pixels_over_time(self, sensor_name, ss_data_frame, resolution, t1_dist_df):
        """Create line plots of distance per zone over time for ToF sensors."""
        times_col = ss_data_frame.iloc[:, 0]
        times_df = pd.DataFrame({"Time": times_col})
        ss_t1_dist_df = pd.concat([times_df, t1_dist_df.fillna(0)], axis=1)

        fig = make_subplots(rows=1, cols=1)
        fig.update_layout(
            title=f"{sensor_name.upper()} - Distance over time per Zone",
            title_x=0.5,
            xaxis_title="Time (s)",
            yaxis_title="Distance [mm]",
            showlegend=True,
        )

        sqrt_res = int(math.sqrt(resolution))
        for i in range(sqrt_res):
            for j in range(sqrt_res - 1, -1, -1):
                index = i * sqrt_res + j
                line_name = f"Z{index}"
                visible = True if index == 0 else "legendonly"
                fig.add_trace(
                    go.Scatter(
                        x=ss_t1_dist_df["Time"],
                        y=ss_t1_dist_df.iloc[:, index + 1],
                        mode="lines",
                        name=line_name,
                        visible=visible,
                        legendgroup=f"group{index%(math.sqrt(resolution))}",
                    )
                )

        fig.update_layout(
            legend=dict(
                orientation="h",
                groupclick="toggleitem",
                itemsizing="constant",
                xanchor="center",
                x=0.5,
            )
        )

        return fig

    def __plot_light_sensor(self, sensor_name, dask_chunk, cols, label):
        """Plot Ambient Light Sensor channels with optional tag regions."""
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Ambient Light Sensor",
            xaxis_title="Time (s)",
            yaxis_title="ADC Count",
            autosize=True,
        )
        fig = go.Figure(layout=layout)
        als_lines_colors = ["#000000", "#FF0000", "#999999", "#0000FF", "#00FF00", "#FF00FF"]
        for i, c in enumerate(cols):
            if c != "Time":
                y_column = c
                color = als_lines_colors[i % len(als_lines_colors)]
                fig.add_trace(
                    go.Scatter(
                        x=dask_chunk["Time"],
                        y=dask_chunk[y_column],
                        mode="lines",
                        name=y_column,
                        line=dict(color=color),
                    )
                )

        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(
                fig, label_time_tags, dask_chunk["Time"].min(), dask_chunk["Time"].max()
            )
        return fig

    def __plot_presence_sensor(
        self, sensor_name, dask_chunk, cols, label, software_compensation, embedded_compensation
    ):
        """Plot presence, motion, and compensation channels with tag/region overlays."""
        # Create a list to store figures to be returned
        figures = []

        # create a new layout for the Ambient & Object (raw) figure
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Ambient & Object (raw)",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True,
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)

        # Plot Ambient and Object (raw) traces
        # Skip the first column (Time); plot next two columns
        # (Tambient (raw), Tobject (raw))
        for idx, c in enumerate(
            cols[1:3]
        ):
            fig.add_trace(
                go.Scatter(
                    x=dask_chunk["Time"],
                    y=dask_chunk[c],
                    mode="lines",
                    name=c,
                    line=dict(color=PlotUtils.lines_colors[idx]),
                )
            )

        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(
                fig, label_time_tags, dask_chunk["Time"].min(), dask_chunk["Time"].max()
            )

        # Add Plot Ambient and Object (raw) figure to the list of figures to be returned
        figures.append(fig)

        # create a new layout for the Presence figure
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Presence",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True,
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)

        line_color = "#335c67"
        line_color_sw = "#bb3e03"
        line_color_emb = "#ff9900"
        if software_compensation or embedded_compensation:
            fig = make_subplots(rows=2, cols=1)
            fig.add_trace(
                go.Scatter(
                    x=dask_chunk["Time"],
                    y=dask_chunk[cols[4]],
                    line=dict(color=line_color),
                    mode="lines",
                    name=cols[4],
                ),
                row=1,
                col=1,
            )
            fig.update_xaxes(title_text="Time (s)", row=1, col=1)
            fig.update_yaxes(title_text="Value", row=1, col=1)
            PlotUtils.draw_regions(fig, dask_chunk, "Presence flag", "#97D3C2", 0.5, 1, 1, False)
            if embedded_compensation:
                fig.add_trace(
                    go.Scatter(
                        x=dask_chunk["Time"],
                        y=dask_chunk[cols[3]],
                        line=dict(color=line_color_emb),
                        mode="lines",
                        name=cols[3],
                    ),
                    row=2,
                    col=1,
                )
                fig.update_xaxes(title_text="Time (s)", row=2, col=1)
                fig.update_yaxes(title_text="Value", row=2, col=1)
            if software_compensation:
                fig.add_trace(
                    go.Scatter(
                        x=dask_chunk["Time"],
                        y=dask_chunk[cols[8]],
                        line=dict(color=line_color_sw),
                        mode="lines",
                        name=cols[8],
                    ),
                    row=2,
                    col=1,
                )
                PlotUtils.draw_regions(
                    fig, dask_chunk, "Presence flag (sw_comp)", "#FDD891", 0.5, 2, 1, False
                )
                fig.update_xaxes(title_text="Time (s)", row=2, col=1)
                fig.update_yaxes(title_text="Value", row=2, col=1)
            fig.update_layout(title_text=f"{sensor_name.upper()} - Presence")
        else:
            fig.add_trace(
                go.Scatter(
                    x=dask_chunk["Time"],
                    y=dask_chunk[cols[34]],
                    line=dict(color=line_color),
                    mode="lines",
                    name=cols[4],
                )
            )
            PlotUtils.draw_regions(
                fig, dask_chunk, "Presence flag", "#97D3C2", 0.5, show_label=False
            )

        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(
                fig, label_time_tags, dask_chunk["Time"].min(), dask_chunk["Time"].max()
            )

        figures.append(fig)

        layout = go.Layout(
            title=f"{sensor_name.upper()} - Motion",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True,
        )
        fig = go.Figure(layout=layout)
        fig = make_subplots(rows=2, cols=1)

        fig.add_trace(
            go.Scatter(
                x=dask_chunk["Time"],
                y=dask_chunk[cols[6]],
                line=dict(color=line_color),
                mode="lines",
                name=cols[6],
            ),
            row=1,
            col=1,
        )
        fig.update_xaxes(title_text="Time (s)", row=1, col=1)
        fig.update_yaxes(title_text="Value", row=1, col=1)
        PlotUtils.draw_regions(fig, dask_chunk, "Motion flag", "#97D3C2", 0.5, 1, 1, False)

        fig.add_trace(
            go.Scatter(
                x=dask_chunk["Time"],
                y=dask_chunk[cols[9]],
                line=dict(color=line_color_sw),
                mode="lines",
                name=cols[9],
            ),
            row=2,
            col=1,
        )
        PlotUtils.draw_regions(
            fig, dask_chunk, "Motion flag (sw_comp)", "#FDD891", 0.5, 2, 1, False
        )
        fig.update_xaxes(title_text="Time (s)", row=2, col=1)
        fig.update_yaxes(title_text="Value", row=2, col=1)

        fig.update_layout(title_text=f"{sensor_name.upper()} - Motion")
        figures.append(fig)

        return figures

    def __plot_mems_audio_sensor(
        self, sensor_name, dask_chunk, cols, dim, subplots, label, raw_flag, unit, fft_params
    ):
        """Plot MEMS/audio timeseries and optional spectrograms.

        - subplots: when True and dim>1, creates per-axis subplots.
        - label: when provided, draws tag rectangles.
        - fft_params: tuple (fft_plots_enabled, odr) enabling lightweight spectrograms.
        """
        # Create a list to store figures to be returned
        figures = []

        # create a new layout for the Ambient & Object (raw) figure
        layout = go.Layout(
            title=sensor_name.upper() if (not raw_flag) else sensor_name.upper() + " (raw)",
            xaxis_title="Time (s)",
            yaxis_title=(
                UnitMap().unit_dict.get(unit, unit) if (not raw_flag and unit is not None) else ""
            ),
            autosize=True,
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)

        if subplots and dim > 1:
            fig = make_subplots(rows=dim, cols=1)
            for i in range(dim):
                fig.add_trace(
                    go.Scatter(
                        x=dask_chunk["Time"],
                        y=dask_chunk[cols[i + 1]],
                        line=dict(color=PlotUtils.lines_colors[i]),
                        mode="lines",
                        name=cols[i + 1],
                    ),
                    row=i + 1,
                    col=1,
                )
                fig.update_xaxes(title_text="Time (s)", row=i + 1, col=1)
                if not raw_flag and unit is not None:
                    fig.update_yaxes(
                        title_text=UnitMap().unit_dict.get(unit, unit), row=i + 1, col=1
                    )
        else:
            # if "_ispu" in sensor_name:
            #     n_lines = len(self.get_mlc_ispu_output_column_names())
            # else:
            n_lines = dim

            for i in range(n_lines):
                fig.add_trace(
                    go.Scatter(
                        x=dask_chunk["Time"],
                        y=dask_chunk[cols[i + 1]],
                        line=dict(color=PlotUtils.lines_colors[i]),
                        mode="lines",
                        name=cols[i + 1],
                    )
                )

        fig.update_layout(
            title_text=sensor_name.upper() if (not raw_flag) else sensor_name.upper() + " (raw)"
        )

        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(
                fig, label_time_tags, dask_chunk["Time"].min(), dask_chunk["Time"].max()
            )

        figures.append(fig)

        if fft_params is not None and ("_acc" in sensor_name or "_mic" in sensor_name):

            odr = fft_params[1]
            window_size = 256
            # overlap = 128
            step = window_size

            def compute_spectrogram(signal, fs, window_size, step):
                n_windows = (len(signal) - window_size) // step + 1
                spec = []
                for i in range(n_windows):
                    start = i * step
                    end = start + window_size
                    windowed = signal[start:end] * np.hanning(window_size)
                    fft_vals = np.fft.rfft(windowed)
                    power = np.abs(fft_vals) ** 2
                    spec.append(power)
                spec = np.array(spec).T  # shape: [frequencies, time]
                times = np.arange(n_windows) * step / fs
                freqs = np.fft.rfftfreq(window_size, d=1 / fs)
                return freqs, times, 10 * np.log10(spec + 1e-12)  # Convert to dB

            if "_mic" in sensor_name:
                freqs, times, spec = compute_spectrogram(
                    dask_chunk[cols[1]].values, odr, window_size, step
                )
                mic_fig = go.Figure(data=go.Heatmap(z=spec, x=times, y=freqs, colorscale="Viridis"))
                mic_fig.update_layout(
                    title=f"{sensor_name.upper()} - Spectrogram",
                    xaxis_title="Time (s)",
                    yaxis_title="Frequency (Hz)",
                )
                figures.append(mic_fig)
            elif "_acc" in sensor_name:
                axes = cols[1:]
                specs = []
                for axis in axes:
                    freqs, times, spec = compute_spectrogram(
                        dask_chunk[axis].values, odr, window_size, step
                    )
                    specs.append((axis, spec))

                acc_mag = np.sqrt(np.sum([dask_chunk[axis].values ** 2 for axis in axes], axis=0))
                freqs, times, spec = compute_spectrogram(acc_mag, odr, window_size, step)
                specs.append(("ACC Magnitude", spec))

                acc_fig = make_subplots(
                    rows=len(specs),
                    cols=1,
                    shared_xaxes=True,
                    subplot_titles=[f"{axis} - Spectrogram" for axis, _ in specs],
                )

                for i, (axis, z) in enumerate(specs, start=1):
                    acc_fig.add_trace(
                        go.Heatmap(
                            z=z,
                            x=times,
                            y=freqs,
                            colorscale="Viridis",
                            showscale=(i == len(specs)),  # Only last subplot gets colorbar
                            colorbar=dict(title="dB") if i == len(specs) else None,
                        ),
                        row=i,
                        col=1,
                    )
                    acc_fig.update_yaxes(title_text="Frequency (Hz)", row=i, col=1)

                acc_fig.update_layout(
                    height=300 * len(specs),
                    title_text=f"{sensor_name.upper()} - Spectrogram",
                    xaxis_title="Time (s)",
                )
                figures.append(acc_fig)

        return figures

    # Plots Helper Functions

    # Plots Functions

    def get_plot_threads(self):
        """
        Returns the list of threads used for plotting.

        Returns:
            list: A list of threads used for plotting.
        """
        return self.plot_threads

    def close_plot_threads(self):
        """
        Closes all plot threads by joining them.
        """
        for t in self.plot_threads:
            t.shutdown()

    def __show_plot_in_browser(self, fig, comp_name, save_plot=False):
        """Serve a Plotly figure via Dash on a free local port and open it in browser.

        Parameters:
        - fig: plotly.graph_objects.Figure to display (or FigureResampler wrapper)
        - comp_name: str used for window title and optional HTML filename
        - save_plot: bool save an HTML snapshot beside the acquisition folder

        Behavior:
        - Uses plotly_resampler to keep interactivity for large datasets (unless heatmap for ToF).
        - Spawns a ServerThread; keeps reference for later shutdown.
        """
        import socket
        import webbrowser
        from plotly_resampler import FigureResampler
        from dash import Dash, dcc, html, Input, Output

        # Function to find a free port
        def find_free_port():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                return s.getsockname()[1]

        # Find a free port
        port = find_free_port()

        # Create a resampler for the figure
        resampler = FigureResampler(fig, default_n_shown_samples=1000)

        # Save plots as HTML files if required
        if save_plot:
            html_file = os.path.join(self.get_acquisition_path(), f"{comp_name}.html")
            resampler.write_html(html_file)

        app = Dash(__name__)
        app.title = f"{comp_name}"

        has_heatmap = any(isinstance(trace, go.Heatmap) for trace in fig.data)
        if "_tof" in comp_name and has_heatmap:
            app.layout = html.Div(
                [
                    dcc.Graph(figure=fig, style={"height": "95vh"}),
                ]
            )
        else:
            app.layout = html.Div(
                [
                    dcc.Graph(id="my-graph", figure=resampler, style={"height": "95vh"}),
                    # Hidden store used as a dummy output for the double-click callback.
                    dcc.Store(id="my-graph-dblclick-reset"),
                ]
            )
            # Register the resampler callback
            resampler.register_update_graph_callback(app, "my-graph")

            # plotly-resampler only re-aggregates the full-resolution data when the
            # relayout event carries both "autorange" and "showspikes" (i.e. the
            # "Reset axes" modebar button). A plain double-click emits only
            # "autorange", so the resampler does nothing and the plot stays stuck at
            # the previously zoomed downsampling. This clientside callback detects the
            # double-click and re-dispatches it as a full "Reset axes" relayout so the
            # behaviour matches the modebar button.
            app.clientside_callback(
                """
                function(relayoutData) {
                    const ns = window.dash_clientside;
                    if (!relayoutData) { return ns.no_update; }
                    const keys = Object.keys(relayoutData);
                    const isAutorange = keys.some(function(k) {
                        return /^xaxis\\d*\\.autorange$/.test(k) && relayoutData[k] === true;
                    });
                    const hasSpikes = keys.some(function(k) {
                        return k.indexOf("showspikes") !== -1;
                    });
                    const hasRange = keys.some(function(k) {
                        return k.indexOf(".range[") !== -1;
                    });
                    // Only act on a plain double-click (autorange without spikes/range).
                    if (!(isAutorange && !hasSpikes && !hasRange)) { return ns.no_update; }

                    const gd = document.getElementById("my-graph");
                    const plot = gd ? (gd.querySelector(".js-plotly-plot") || gd) : null;
                    if (!plot || !plot.layout || !window.Plotly) { return ns.no_update; }

                    // Build a relayout that mimics the "Reset axes" button: autorange
                    // every axis and include a "showspikes" key for each x-axis so that
                    // plotly-resampler treats it as a full reset and re-aggregates.
                    const update = {};
                    Object.keys(plot.layout).forEach(function(k) {
                        if (/^xaxis\\d*$/.test(k)) {
                            update[k + ".autorange"] = true;
                            update[k + ".showspikes"] =
                                (plot.layout[k] && plot.layout[k].showspikes) || false;
                        } else if (/^yaxis\\d*$/.test(k)) {
                            update[k + ".autorange"] = true;
                        }
                    });
                    if (Object.keys(update).length) {
                        window.Plotly.relayout(plot, update);
                    }
                    return ns.no_update;
                }
                """,
                Output("my-graph-dblclick-reset", "data"),
                Input("my-graph", "relayoutData"),
                prevent_initial_call=True,
            )

        pt = ServerThread(app, port)
        pt.start()
        self.plot_threads.append(pt)

        webbrowser.open(f"http://127.0.0.1:{port}")

    # Stream Parquet row-groups to keep memory low (requires fastparquet)
    def _iter_parquet_row_groups(self, path: str, columns=None):
        """
        Generator that yields pandas DataFrames for each row group in a Parquet file.
        Parameters: 
            -path: str path to the Parquet file
            -columns: list of str column names to read (default: all columns)
        Yields:
            - pandas.DataFrame for each row group
        """
        if ParquetFile is None:
            raise RuntimeError("fastparquet not installed. Please run: pip install fastparquet")
        pf = ParquetFile(path)
        # Prefer the iterator API to avoid version-specific kwargs
        if hasattr(pf, "iter_row_groups"):
            for df in pf.iter_row_groups(columns=columns):
                yield df
            return
        # Fallback: return the whole file as a single DataFrame
        yield pf.to_pandas(columns=columns)

    def get_dask_df(
        self,
        comp_name,
        comp_status,
        start_time=0,
        end_time=-1,
        label=None,
        raw_flag=False,
    ):
        """
        Return a Dask DataFrame for a component using parquet conversion for chunked processing.
        Note: To use Dask, the 'dask' package must be installed. If Dask is not available,
        the method will return an iterator of pandas DataFrames for each parquet row group.
        Parameters:
        - comp_name: str component name
        - comp_status: dict component status
        - start_time: float start time in seconds
        - end_time: float end time in seconds
        - label: optional str label name for tagging
        - raw_flag: bool whether to extract raw data
        Returns:
        - Dask DataFrame or None if error occurs
        """
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{comp_name}.parquet')
            # Convert data to parquet format if the file does not exist
            if not os.path.exists(file_path):
                HSDatalog.convert_dat_to_xsv(
                    self,
                    {comp_name: comp_status},
                    start_time,
                    end_time,
                    labeled,
                    raw_flag,
                    self.get_acquisition_path(),
                    "PARQUET",
                )
            # Try Dask only if available; else return an iterator of pandas DataFrames
            try:
                import dask.dataframe as dd  # lazy import
                return dd.read_parquet(file_path)
            except Exception:
                if os.path.exists(file_path):
                    return self._iter_parquet_row_groups(file_path)
                return None
        except Exception as err:
            log.exception(err)
            return None

    def get_sensor_plot(
        self,
        sensor_name,
        sensor_status,
        start_time=0,
        end_time=-1,
        label=None,
        which_tags=[],
        subplots=False,
        raw_flag=False,
        fft_plots=False,
        save_plots=False,
    ):
        """Generate and serve plots for a sensor component.

        Parameters reflect plotting options:
        - label/which_tags: tag overlay control
        - subplots/raw_flag: layout and scaling
        - fft_plots: include spectrograms for acc/mic
        - save_plots: write HTML files to acquisition folder

        Note: which_tags is currently unused. It will be implemented in future releases.
        with this parameter, users will be able to filter which tags to display on the plots.
        In the current implementation, only a label name is used to fetch all associated tags.

        Behavior:
        - Converts .dat to parquet for chunked processing.
        - Builds figures according to sensor_category and serves them via Dash.
        """
        _ = which_tags  # currently unused
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{sensor_name}.parquet')
            # Convert data to parquet format (overwriting it if already exists)
            HSDatalog.convert_dat_to_xsv(
                self,
                {sensor_name: sensor_status},
                start_time,
                end_time,
                labeled,
                raw_flag,
                self.get_acquisition_path(),
                "PARQUET",
            )
            # Check if the parquet file exists
            if not os.path.exists(file_path):
                return
            # Prefer Dask if installed; else stream row-groups with fastparquet
            use_dask = False
            try:
                import dask.dataframe as dd  # lazy import
                dask_df = dd.read_parquet(file_path)
                use_dask = True
            except Exception:
                dask_df = None

            # Get the acquisition label classes
            acq_label_classes = self.get_acquisition_label_classes()
            # Filter columns to plot
            columns_to_plot = None
            if use_dask:
                columns_to_plot = [
                    c for c in dask_df.columns if c not in (acq_label_classes or [])
                ]

            if save_plots:
                # Define the output directory for saving plots
                output_dir = "plots"
                os.makedirs(output_dir, exist_ok=True)

            sensor_category = sensor_status.get("sensor_category")
            figures = []

            if use_dask:
                # Dask path (unchanged behavior)
                for delayed_chunk in dask_df.to_delayed():
                    chunk = delayed_chunk.compute()
                    columns_to_plot = [
                        c for c in chunk.columns if c not in (acq_label_classes or [])
                    ]

                    # Uncomment the following lines to log the size of each chunk
                    # with open("dask_log.txt", "a") as log_file:
                    #     log_file.write(
                    #         f"Processing chunk for sensor: {sensor_name}, "
                    #         f"size: {len(chunk)} rows, partitions: {dask_df.partitions}\n"
                    #     )

                    if sensor_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                        resolution = sensor_status.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                            output_format = sensor_status.get("output_format")
                            figures = self.__plot_ranging_sensor(
                                sensor_name,
                                chunk,
                                res,
                                output_format,
                            )
                    elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                        fig = self.__plot_light_sensor(sensor_name, chunk, columns_to_plot, label)
                        figures.append(fig)
                    elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                        figures = self.__plot_presence_sensor(
                            sensor_name,
                            chunk,
                            columns_to_plot,
                            label,
                            sensor_status.get("software_compensation"),
                            sensor_status.get("embedded_compensation"),
                        )
                    else: # ISENSOR_CLASS_MEMS and ISENSOR_CLASS_AUDIO
                        fft_params = None
                        if fft_plots:
                            odr = sensor_status.get('odr', 1)
                            fft_params = (fft_plots, odr)
                        figures = self.__plot_mems_audio_sensor(
                            sensor_name,
                            chunk,
                            columns_to_plot,
                            len(columns_to_plot) - 1,
                            subplots,
                            label,
                            raw_flag,
                            sensor_status.get('unit'),
                            fft_params,
                        )
                    for fig in figures:
                        self.__show_plot_in_browser(fig, sensor_name, save_plot=save_plots)
            else:
                # fastparquet streaming fallback
                for chunk in self._iter_parquet_row_groups(file_path):
                    columns_to_plot = [
                        c for c in chunk.columns if c not in (acq_label_classes or [])
                    ]
                    if sensor_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                        resolution = sensor_status.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                            output_format = sensor_status.get("output_format")
                            figures = self.__plot_ranging_sensor(
                                sensor_name,
                                chunk,
                                res,
                                output_format,
                            )
                    elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                        fig = self.__plot_light_sensor(sensor_name, chunk, columns_to_plot, label)
                        figures.append(fig)
                    elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                        figures = self.__plot_presence_sensor(
                            sensor_name,
                            chunk,
                            columns_to_plot,
                            label,
                            sensor_status.get("software_compensation"),
                            sensor_status.get("embedded_compensation"),
                        )
                    else:
                        fft_params = None
                        if fft_plots:
                            odr = sensor_status.get('odr', 1)
                            fft_params = (fft_plots, odr)
                        figures = self.__plot_mems_audio_sensor(
                            sensor_name,
                            chunk,
                            columns_to_plot,
                            len(columns_to_plot) - 1,
                            subplots,
                            label,
                            raw_flag,
                            sensor_status.get('unit'),
                            fft_params,
                        )
                    for fig in figures:
                        self.__show_plot_in_browser(fig, sensor_name, save_plot=save_plots)

            # Delete the Parquet file after processing
            if os.path.exists(file_path):
                os.remove(file_path)

        except MissingISPUOutputDescriptorException as ispu_err:
            # Handle missing ISPU output descriptor exception
            log.error(ispu_err)
            log.warning(
                "Please copy the right ISPU configuration file (JSON) in your "
                f'"{self.get_acquisition_path()}" '
                'acquisition folder.'
            )
        except Exception as err:
            log.exception(err)

    def get_actuator_plot(
        self,
        actuator_name,
        actuator_status,
        start_time=0,
        end_time=-1,
        label=None,
        which_tags=[],
        subplots=True,
        raw_flag=False,
        save_plots=False,
    ):
        """Proxy to get_sensor_plot for actuator components (subplots default True)."""
        subplots=True
        self.get_sensor_plot(
            actuator_name,
            actuator_status,
            start_time,
            end_time,
            label,
            which_tags,
            subplots,
            raw_flag,
            save_plots=save_plots,
        )

    def get_algorithm_plot(
        self,
        algorithm_name,
        algorithm_status,
        start_time=0,
        end_time=-1,
        label=None,
        which_tags=[],
        subplots=False,
        raw_flag=False,
    ):
        """Generate and serve plots for an algorithm component (FFT supported).

        - For FFT, renders a spectrogram heatmap (dB) using fft_sample_freq and fft_length.
        Note: which_tags, subplots are unused at the moment, but kept for API consistency.
        """
        _ = which_tags  # Unused
        _ = subplots  # Unused
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{algorithm_name}.parquet')
            # Convert data to parquet format (overwriting it if already exists)
            HSDatalog.convert_dat_to_xsv(
                self,
                {algorithm_name: algorithm_status},
                start_time,
                end_time,
                labeled,
                raw_flag,
                self.get_acquisition_path(),
                "PARQUET",
            )
            # Check if the parquet file exists
            if not os.path.exists(file_path):
                return
            # Prefer Dask if installed; else stream row-groups with fastparquet
            use_dask = False
            try:
                import dask.dataframe as dd  # lazy import
                dask_df = dd.read_parquet(file_path)
                use_dask = True
            except Exception:
                dask_df = None

            if use_dask:
                algo_type = algorithm_status.get("algorithm_type")
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    s_dim = algorithm_status.get("fft_length")
                else:
                    s_dim = algorithm_status.get("dim")
                # Get the acquisition label classes
                acq_label_classes = self.get_acquisition_label_classes()
                # Filter columns to plot
                # columns_to_plot = [
                #     item for item in dask_df.columns if item not in acq_label_classes
                # ]
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    fft_length = s_dim
                    fft_sample_freq = algorithm_status.get("fft_sample_freq")
                    if fft_sample_freq is None:
                        log.error("FFT Sample Freq. unknown")
                        raise MissingPropertyError("fft_sample_freq")
                    for chunk in dask_df.to_delayed():
                        chunk = chunk.compute()
                        # Prepare data for spectrogram
                        df_array = chunk.iloc[:, 1:].T.to_numpy(dtype="float")
                        y_value = np.square(df_array)
                        y_value = y_value / (fft_length * fft_sample_freq)
                        y_value = 10 * np.log10(y_value)
                        freqs = np.linspace(0, fft_sample_freq / 2, y_value.shape[0])
                        # Plotly spectrogram
                        fig = go.Figure(
                            data=go.Heatmap(
                                z=y_value,
                                y=freqs,
                                colorscale="Viridis",
                                colorbar=dict(
                                    title="dB",
                                    len=1.0,      # Full subplot height
                                    y=0.5,        # Centered vertically
                                    yanchor='middle'
                                )
                            )
                        )
                        fig.update_layout(
                            title=f"{algorithm_name.upper()} - Spectrogram",
                            yaxis_title="Frequency (Hz)",
                            height=600
                        )
                        self.__show_plot_in_browser(fig, algorithm_name)
                else:
                    log.error("Algorithm type selected is not supported.")
            else:
                # fastparquet streaming fallback for FFT only
                algo_type = algorithm_status.get("algorithm_type")
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    s_dim = algorithm_status.get("fft_length")
                    fft_sample_freq = algorithm_status.get("fft_sample_freq")
                    if fft_sample_freq is None:
                        log.error("FFT Sample Freq. unknown")
                        raise MissingPropertyError("fft_sample_freq")
                    for chunk in self._iter_parquet_row_groups(file_path):
                        df_array = chunk.iloc[:, 1:].T.to_numpy(dtype="float")
                        y_value = np.square(df_array)
                        y_value = y_value / (s_dim * fft_sample_freq)
                        y_value = 10 * np.log10(y_value)
                        freqs = np.linspace(0, fft_sample_freq / 2, y_value.shape[0])
                        fig = go.Figure(
                            data=go.Heatmap(
                                z=y_value,
                                y=freqs,
                                colorscale="Viridis",
                                colorbar=dict(
                                    title="dB",
                                    len=1.0,
                                    y=0.5,
                                    yanchor="middle",
                                ),
                            )
                        )
                        fig.update_layout(
                            title=f"{algorithm_name.upper()} - Spectrogram",
                            yaxis_title="Frequency (Hz)",
                            height=600,
                        )
                        self.__show_plot_in_browser(fig, algorithm_name)
                else:
                    log.error("Algorithm type selected is not supported.")

            # Delete the Parquet file after processing
            if os.path.exists(file_path):
                os.remove(file_path)

        except MissingPropertyError as exc:
            log.error(f"Missing {exc} Property Error!")
            raise
        except MemoryError:
            log.error(
                f"Memory Error occoured! You should batch process your "
                f"{FileManager.encode_file_name(algorithm_name)} file"
            )
            raise
        except  ValueError:
            log.error(
                f"Value Error occoured! You should batch process your "
                f"{FileManager.encode_file_name(algorithm_name)} file"
            )
            raise

    # Plots Functions

    # === OFFLINE CLI Interaction ===
    def prompt_device_id_select_CLI(self, device_list):
        """Prompt to select a device by index; return selected index."""
        selected_device = CLI.select_item("Device", device_list)
        selected_device_id = device_list.index(selected_device)
        return selected_device_id

    def prompt_sensor_select_CLI(self, sensor_list=None):
        """Prompt to select a sensor component; returns the selected dict entry."""
        if sensor_list is None:
            sensor_list = self.get_sensor_list()
        return CLI.select_item("PnPL_Component", sensor_list)

    def prompt_algorithm_select_CLI(self, algo_list=None):
        """Prompt to select an algorithm component."""
        if algo_list is None:
            algo_list = self.get_algorithm_list()
        return CLI.select_item("PnPL_Component", algo_list)

    def prompt_actuator_select_CLI(self, actuator_list=None):
        """Prompt to select an actuator component."""
        if actuator_list is None:
            actuator_list = self.get_actuator_list()
        return CLI.select_item("PnPL_Component", actuator_list)

    def prompt_actuator_telemetries_select_CLI(self, actuator_stat, telemetry_keys):
        """Prompt to select telemetry keys from an actuator status dict."""
        telemetry_list = []
        for tk in telemetry_keys:
            telemetry_list.append({tk: actuator_stat[tk]})
        return CLI.select_items("PnPL_Component", telemetry_list)

    def prompt_component_select_CLI(self, component_list=None):
        """Prompt to select any component (sensor+algorithm merged list)."""
        if component_list is None:
            sensor_list = self.get_sensor_list()
            algo_list = self.get_algorithm_list()
            component_list = sensor_list + algo_list
        return CLI.select_item("PnPL_Component", component_list)

    def prompt_file_select_CLI(self, dat_file_list=None):
        """Prompt to select a dat file from model expectation or folder listing."""
        if dat_file_list is None:
            dat_file_list = FileManager.get_file_names_from_model(self.device_model)
        return CLI.select_item("Data File", dat_file_list)

    def prompt_label_select_CLI(self, label_list=None):
        """Prompt to select a tag label from acquisition_info or provided list."""
        if label_list is None or len(label_list) == 0:
            label_list = self.get_acquisition_label_classes()
        return CLI.select_item("Labels", label_list)

    def present_device_info(self, device_info=None):
        """
        Presents the device information to the user interface, typically through the
        command line interface (CLI). If no device information is provided, it retrieves
        the default device information using the get_device_info method.
        :param device_info: The device information to be presented.
            If None, the method retrieves the device information using the get_device_info method.
        """
        # Check if device information has been provided
        if device_info is None:
            # If not, retrieve the default device information using the 'get_device_info' method
            device_info = self.get_device_info()
        # Use the CLI (Command Line Interface) module to present the device information.
        # This could involve printing the information to the console, displaying it in a GUI, etc.
        CLI.present_item(device_info)

    def present_sensor_list(self, sensor_list=None):
        """
        Presents a list of sensors to the user interface, typically through the
        command line interface (CLI). If no sensor list is provided, it retrieves the default
        sensor list using the get_sensor_list method.
        :param sensor_list: [Optional] A list of sensors to be presented.
            If None, the method retrieves the sensor list using the get_sensor_list method.
        """
        # Check if a sensor list has been provided
        if sensor_list is None:
            # If not, retrieve the default sensor list using the 'get_sensor_list' method
            sensor_list = self.get_sensor_list()

        # Use the CLI (Command Line Interface) module to present the items in the sensor list.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(sensor_list)

    def present_sw_tag_classes(self, tag_class_list=None):
        """
        Presents a list of software tag classes to the user interface, typically through the
        command line interface (CLI).cIf no list is provided, it retrieves the software tag
        classes using the get_sw_tag_classes method.

        :param tag_class_list: [Optional] A list of software tag classes to be presented.
            If None, the method retrieves the list using the get_sw_tag_classes method.
        """
        # Check if a list of software tag classes has been provided
        if tag_class_list is None:
            # If not, retrieve the list using the 'get_sw_tag_classes' method
            tag_class_list = self.get_sw_tag_classes()

        # Use the CLI (Command Line Interface) module to present the list of software tag classes.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(tag_class_list)

    def present_hw_tag_classes(self, tag_class_list=None):
        """
        Presents a list of hardware tag classes to the user interface, typically through the
        command line interface (CLI). If no list is provided, it retrieves the hardware tag
        classes using the get_hw_tag_classes method.

        :param tag_class_list: [Optional] A list of hardware tag classes to be presented.
            If None, the method retrieves the list using the get_hw_tag_classes method.
        """
        # Check if a list of hardware tag classes has been provided
        if tag_class_list is None:
            # If not, retrieve the list using the 'get_hw_tag_classes' method
            tag_class_list = self.get_hw_tag_classes()
        # Use the CLI (Command Line Interface) module to present the list of hardware tag classes.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(tag_class_list)

    def present_sensor(self, sensor):
        """
        Presents information about a specific sensor to the user interface, typically through the
        command line interface (CLI). If the sensor information is provided, it is presented using
        the CLI module. If not, a warning is issued.

        :param sensor: The sensor information to be presented. If None,
            a warning message is displayed.
        """
        # Check if sensor information has been provided
        if sensor is not None:
            # If sensor information is available, use the CLI (Command Line Interface)
            # module to present it.
            # This could involve printing the information to the console,
            # displaying it in a GUI, etc.
            CLI.present_item(sensor)
        else:
            # If no sensor information is provided, display a warning message to the user.
            log.warning("No sensor selected")

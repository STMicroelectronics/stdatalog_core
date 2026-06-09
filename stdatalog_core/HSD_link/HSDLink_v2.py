# *****************************************************************************
#  * @file    HSDLink_v2.py
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
"""
High-Level Interface for STDatalog Devices (v2)

Overview:
    - Single entry point to interact with STDatalog/PnPL devices from Python.
    - Wraps command-set specific managers and exposes a small, stable surface API.

Key features:
    - Device discovery, presentation strings, firmware and component status queries.
    - Sensor/algorithm properties (enable, odr, fs, measodr, samples_per_ts, aop, offsets).
    - Logging control (RTC time, start/stop, timestamped acquisition folders, optional saving).
    - Software tag classes (list, label, enable/status, set on/off by name or id).
    - Configuration persistence (device_config.json, acquisition_info.json) for each session.

Backends:
    - "st_hsd" or "pnpl" -> PnPLHSD_Creator (USB/COM with PnPL command set).
    - "st_serial_datalog" -> PnPLSTSRL_Creator (serial datalog firmware).
    - See class HSDLink_v2_Serial for serial-specific overrides and helpers.

Typical workflow:
    1) Create link: HSDLink_v2(...).
    2) Optionally open() with com_id/com_speed (serial), else default open().
    3) start_log(...), interact with the device, then stop_log(...).
    4) Save JSON metadata via save_json_device_file()/save_json_acq_info_file().

Saved files:
    - device_config.json: device status with acquisition_info removed; uuid moved to top-level.
    - acquisition_info.json: session info (name, description, tags, etc.).

Error handling:
    - Raises InvalidCommandSetError for unknown dev_com_type.
    - I/O and JSON errors while saving are propagated to the caller.

State and threading:
    - Maintains minimal state (base/acquisition folders, save_files flag, device count cache).
    - Not thread-safe; serialize calls per device unless com_manager states otherwise.

Compatibility:
    - Device templates can be injected via set_device_template().
    - update_device() can auto-load DTDL models via DeviceCatalogManager (board_id/fw_id).

Examples:
    - Basic USB/PnPL usage:
            link = HSDLink_v2(dev_com_type="st_hsd")
            link.open()
            link.start_log(d_id=0)
            # ... read properties or data ...
            link.stop_log(d_id=0)
            link.save_json_device_file(d_id=0)
            link.save_json_acq_info_file(d_id=0)
            link.close()

    - Serial datalog variant:
            link = HSDLink_v2_Serial()
            link.open(com_id="COM3", com_speed=921600)
"""
import os
import json
import time
from datetime import datetime

from stdatalog_pnpl.PnPLCmd import PnPLCMDManager
from stdatalog_pnpl.DTDL.device_template_manager import DeviceCatalogManager, DeviceTemplateManager

from stdatalog_core.HSD_utils.exceptions import InvalidCommandSetError, WrongDeviceConfigFile, \
    MissingDeviceModelError
from stdatalog_core.HSD_link.communication.PnPL_HSD.PnPLHSD_com_manager import PnPLHSD_Creator
import stdatalog_core.HSD_utils.logger as logger
from stdatalog_core.HSD_link.communication.PnPL_STSRL.PnPLSTSRL_com_manager import PnPLSTSRL_Creator, PnPLSTSRLComManagerError


log = logger.get_logger(__name__)

class HSDLinkV2SerialError(Exception):
    """High-level serial link error raised by HSDLink_v2."""
    pass

class HSDLink_v2:
    """High-Level Interface for STDatalog Devices (v2)

    Responsibilities:
        - Instantiate the appropriate communication manager (PnPL/HSD or serial).
        - Expose device/sensor/algorithm queries and property setters.
        - Manage acquisition folders and save device/acquisition JSON files.
        - Provide logging control and tag helpers.

    Lifecycle:
        1) __init__ optionally updates the device catalog,
            creates com_manager, and checks devices count.
        2) open()/close() delegate to com_manager.
        3) start_log()/stop_log() manage RTC time, folders, and call com_manager.
        4) save_json_* write out status and acquisition information.

    Attributes:
        __com_manager: underlying communication manager instance.
        __base_acquisition_folder: root path for acquisitions (user-provided or ".").
        acquisition_folder: current acquisition session folder (timestamped by default).
        sensor_data_counts: user-visible dict for external tracking (not enforced here).
        nof_connected_devices: cached number of detected devices.
        save_files: enable/disable JSON file saving on start/stop.

    Errors:
        - InvalidCommandSetError raised for unknown dev_com_type.
    """

    __com_manager = None
    __base_acquisition_folder = None
    # __acquisition_folder = None

    def __init__(
        self,
        dev_com_type: str = "st_hsd",
        acquisition_folder=None,
        plug_callback=None,
        unplug_callback=None,
        update_catalog=True,
    ):
        """
        Initialize the link, create the communication manager, set folders, and cache device count.

        Parameters:
            dev_com_type: str
                Communication stack type ('st_hsd', 'pnpl', 'st_serial_datalog').
                Note: 'st_hsd' and 'pnpl' map to the same PnPLHSD_Creator. The difference is
                    historical and both are supported for compatibility.
            acquisition_folder: Optional[str]
                Base path where acquisition subfolders will be created (default ".").
            plug_callback / unplug_callback: Optional[Callable]
                Optional hot-plug notifications from com_manager, if supported.
            update_catalog: bool
                If True, refresh the DeviceCatalog to get up-to-date DTDL models.

        Notes:
            - If no devices are connected (nof_connected_devices <= 0), constructor returns early.
        """

        # STANDARD COMPONENT NAMES
        # These are the default component and field names expected in the device status
        # of STDatalog official devices; these devices encode data as specified by the
        # HSD payload format.
        # They can be overwritten using the dedicated methods given by this class.
        # Examples:
        # - self.set_custom_device_info_component_name(name: str)
        # - self.set_custom_firmware_info_component_name(name: str)
        # - self.set_custom_acquisition_info_component_name(name: str)
        # - self.set_custom_acquisition_uuid_field_name(name: str)
        self.device_info_name = "DeviceInformation"
        self.firmware_info_name = "firmware_info"
        self.acquisition_info_name = "acquisition_info"
        self.acquisition_uuid_field_name = "uuid"
        self.acquisition_name_field_name = "name"
        self.acquisition_description_field_name = "description"
        self.measured_odr_field_name = "measodr"
        self.dimensions_field_name = "dim"
        self.initial_offset_field_name = "ioffset"
        self.annotation_field_name = "sensor_annotation"

        # Update the device catalog if the update_catalog flag is set to True
        if update_catalog:
            DeviceCatalogManager.update_catalog()

        self.__create_com_manager(dev_com_type, plug_callback, unplug_callback)

        self.acquisition_folder = None
        self.sensor_data_counts = {}
        self.nof_connected_devices = 0
        self.save_files = True
        self.is_datalog1 = False
        self.is_vdatalog = False

        self.__dt_manager = None
        if acquisition_folder is None:
            self.__base_acquisition_folder = "."
        else:
            self.__base_acquisition_folder = acquisition_folder

        # check that there is at least one connected device
        self.nof_connected_devices = self.get_nof_devices()
        if self.nof_connected_devices <= 0:
            return

    def update_base_acquisition_folder(self, new_acquisition_folder):
        """
        Update the base folder used for acquisitions.

        If None is provided, it falls back to ".".

        This does not change the already-open acquisition_folder, only the base root
        used the next time start_log() is called.
        """
        if new_acquisition_folder is not None:
            self.__base_acquisition_folder = new_acquisition_folder
        else:
            self.__base_acquisition_folder = "."

    def set_device_template(self, dev_template_json: dict):
        """
        Inject a specific Device Template (DTDL JSON) into the link.

        Useful when a device catalog lookup is not desired or for custom models.
        """
        self.__dt_manager = DeviceTemplateManager(dev_template_json)

    def open(self, com_id=None, com_speed=None, timeout=None):
        """
        Open the underlying com_manager connection.

        Optional:
            - com_id: port or device id
            - com_speed: baud rate or specific speed parameter (only for serial)
            - timeout: connection timeout in seconds (only for serial)

        If not provided, calls the default open() of com_manager.
        """
        if com_id is None or com_speed is None:
            return self.__com_manager.open()
        return self.__com_manager.open(com_id, com_speed, timeout=timeout)

    def close(self):
        """Close the underlying com_manager connection."""
        return self.__com_manager.close()

    def __create_com_manager(self, dev_com_type, plug_callback=None, unplug_callback=None):
        """
        Factory method to create the appropriate command/communication manager.

        dev_com_type:
            - 'pnpl' or 'st_hsd' -> PnPLHSD_Creator
            - 'st_serial_datalog' -> PnPLSTSRL_Creator

        Raises:
            InvalidCommandSetError for unknown dev_com_type.

        Side effects:
            - Logs command set presentation string via logger.
        """
        if dev_com_type == "pnpl":
            factory = PnPLHSD_Creator()
            self.__com_manager = factory.create_cmd_manager(plug_callback, unplug_callback)
        if dev_com_type == "st_hsd":
            factory = PnPLHSD_Creator()
            self.__com_manager = factory.create_cmd_manager(plug_callback, unplug_callback)
        elif dev_com_type == "st_serial_datalog":
            factory = PnPLSTSRL_Creator()
            self.__com_manager = factory.create_cmd_manager()
        else:
            log.error(f"Invalid Command Set selected: {dev_com_type}")
            raise InvalidCommandSetError(dev_com_type)
        # Command set presentation
        log.info(self.get_cmd_set_presentation_string())

    def get_com_manager(self):
        """Return the underlying communication manager for advanced/custom operations."""
        return self.__com_manager

    def get_device_presentation_string(self, d_id: int):
        """Return a human-readable presentation string for the given device id."""
        return self.__com_manager.get_device_presentation_string(d_id)

    def get_cmd_set_presentation_string(self):
        """Return a human-readable presentation for the active command set."""
        return self.__com_manager.get_cmd_set_presentation_string()

    def get_acquisition_folder(self):
        """
        Return the current acquisition folder if a session is active,
        otherwise return the base acquisition folder.
        """
        if self.acquisition_folder is None:
            return self.__base_acquisition_folder
        return self.acquisition_folder

    def get_base_acquisition_folder(self):
        """Return the base acquisition folder path."""
        return self.__base_acquisition_folder

    def get_nof_devices(self):
        """Query the number of connected devices via com_manager."""
        return self.__com_manager.get_nof_devices()

    def get_version(self):
        """Return com_manager version string/info."""
        return self.__com_manager.get_version()

    def get_firmware_info(self, d_id: int):
        """Return firmware info component status for the device."""
        return self.__com_manager.get_component_status(d_id, self.firmware_info_name)

    def get_device_info(self, d_id: int):
        """Return device information component status for the device."""
        return self.__com_manager.get_component_status(d_id, self.device_info_name)

    def get_devices(self):
        """Return a list/structure describing connected devices."""
        return self.__com_manager.get_devices()

    def get_device_status(self, d_id: int):
        """Return the full device status JSON for the given device id."""
        return self.__com_manager.get_device_status(d_id)

    def get_device(self, d_id: int):
        """Alias of get_device_status."""
        return self.get_device_status(d_id)

    def get_component_status(self, d_id: int, component_name: str):
        """
        Return the status for the specified component.

        Defensive: returns None on underlying errors.
        """
        try:
            return self.__com_manager.get_component_status(d_id, component_name)
        except Exception:
            return None

    def get_device_alias(self, d_id: int):
        """Return device alias string, if supported."""
        return self.__com_manager.get_device_alias(d_id)

    def get_sensors_count(self, d_id: int, only_active: bool = None):
        """Return the number of sensor components (optionally only active ones)."""
        return self.__com_manager.get_sensor_components_count(d_id, only_active)

    def get_sensors_names(self, d_id: int, type_filter: str = "", only_active: bool = False):
        """
        Return sensor component names.

        type_filter: optional filter string (implementation specific).
        only_active: if True, include only enabled sensors.
        """
        return self.__com_manager.get_sensor_components_names(d_id, type_filter, only_active)

    def get_sensors(self, d_id: int, type_filter: str = "", only_active: bool = False):
        """Return sensor components status with optional filtering."""
        return self.__com_manager.get_sensor_components_status(d_id, type_filter, only_active)

    def get_algorithms_count(self, d_id: int, only_active: bool = None):
        """Return the number of algorithm components (optionally only active ones)."""
        return self.__com_manager.get_algorithm_components_count(d_id, only_active)

    def get_algorithms_names(self, d_id: int, only_active: bool = False):
        """Return algorithm component names (optionally only active)."""
        return self.__com_manager.get_algorithm_components_names(d_id, only_active)

    def get_sensor_enable(self, d_id: int, sensor_name: str):
        """Return boolean 'enable' property for a sensor component."""
        return self.__com_manager.get_boolean_property(d_id, sensor_name, "enable")

    def get_sensor_odr(self, d_id: int, sensor_name: str):
        """Return float 'odr' (Output Data Rate) property for a sensor component."""
        return self.__com_manager.get_float_property(d_id, sensor_name, "odr")

    def get_sensor_measured_odr(self, d_id: int, sensor_name: str):
        """Return float 'measodr' (measured ODR) property for a sensor component."""
        return self.__com_manager.get_float_property(d_id, sensor_name, self.measured_odr_field_name)

    def get_sensor_fs(self, d_id: int, sensor_name: str):
        """Return float 'fs' (full scale) property for a sensor component."""
        return self.__com_manager.get_float_property(d_id, sensor_name, "fs")

    def get_sensor_aop(self, d_id: int, sensor_name: str):
        """Return float 'aop' (Acoustic Overload Point) for microphone-like components."""
        return self.__com_manager.get_float_property(d_id, sensor_name, "aop")

    def get_sensor_samples_per_ts(self, d_id: int, sensor_name: str):
        """
        Return integer 'samples_per_ts' property.

        Defensive: some models store value under 'val' sub-property; try both.
        """
        try:
            return self.__com_manager.get_integer_property(d_id, sensor_name, "samples_per_ts")
        except Exception:
            return self.__com_manager.get_integer_property(
                d_id, sensor_name, "samples_per_ts", "val"
            )

    def get_sensor_initial_offset(self, d_id: int, sensor_name: str):
        """Return float initial offset for the sensor component."""
        return self.__com_manager.get_float_property(d_id, sensor_name, self.initial_offset_field_name)

    def get_sensors_status(self, d_id: int, type_filter: str = "", only_active: bool = False):
        """Alias to get sensor components status with optional filtering."""
        return self.__com_manager.get_sensor_components_status(d_id, type_filter, only_active)

    def get_algorithms_status(self, d_id: int, only_active: bool = False):
        """Return algorithm components status (optionally only active ones)."""
        return self.__com_manager.get_algorithm_components_status(d_id, only_active)

    def get_acquisition_info(self, d_id: int):
        """Return acquisition_info component status for the device."""
        return self.__com_manager.get_component_status(d_id, self.acquisition_info_name)

    def get_tags_info(self, d_id: int):
        """Return tags_info component status for the device."""
        return self.__com_manager.get_component_status(d_id, "tags_info")

    def get_tag_list(self, d_id: int):
        """Return list of tags from tags_info, or empty list if none."""
        tags_info = self.get_tags_info(d_id)
        if "tags" in tags_info:
            return tags_info["tags"]
        return list()

    def get_max_tags_per_acq(self, d_id: int):
        """Return max number of tags allowed per acquisition session."""
        tags_info = self.get_tags_info(d_id)
        if "max_tags_num" in tags_info["tags_info"]:
            return tags_info["tags_info"]["max_tags_num"]
        return None

    def get_sw_tag_classes(self, d_id: int):
        """
        Return a dictionary of all software tag classes from tags_info.
        Defensive: returns empty dict if no tags found.
        """
        sw_tags = {}
        res = self.get_tags_info(d_id)
        tags = res.get("tags_info")
        if tags is not None:
            for t in res["tags_info"]:
                if "sw" in t:
                    sw_tags[t] = res["tags_info"][t]
        return sw_tags

    def get_sw_tag_class(self, d_id: int, tag_class_name: str):
        """Return a specific software tag class by name from tags_info."""
        ret = self.__com_manager.get_string_property(d_id, "tags_info", tag_class_name)
        return ret

    def get_sw_tag_class_label(self, d_id: int, tag_class_name: str):
        """Return the label of a specific software tag class by name from tags_info."""
        ret = self.__com_manager.get_string_property(d_id, "tags_info", tag_class_name, "label")
        return ret

    def get_sw_tag_class_enabled(self, d_id: int, tag_class_name: str):
        """Return the enabled status of a specific software tag class by name from tags_info."""
        ret = self.__com_manager.get_boolean_property(d_id, "tags_info", tag_class_name, "enabled")
        return ret

    def get_sw_tag_class_status(self, d_id: int, tag_class_name: str):
        """Return the status of a specific software tag class by name from tags_info."""
        ret = self.__com_manager.get_boolean_property(d_id, "tags_info", tag_class_name, "status")
        return ret

    def get_sw_tag_class_by_id(self, d_id: int, tag_class_id: int):
        """Return a specific software tag class by id from tags_info."""
        ret = self.__com_manager.get_string_property(d_id, "tags_info", f"sw_tag{tag_class_id}")
        return ret

    def get_sw_tag_class_label_by_id(self, d_id: int, tag_class_id: int):
        """Return the label of a specific software tag class by id from tags_info."""
        ret = self.__com_manager.get_string_property(
            d_id, "tags_info", f"sw_tag{tag_class_id}", "label"
        )
        return ret

    def get_sw_tag_class_enabled_by_id(self, d_id: int, tag_class_id: int):
        """Return the enabled status of a specific software tag class by id from tags_info."""
        ret = self.__com_manager.get_boolean_property(
            d_id, "tags_info", f"sw_tag{tag_class_id}", "enabled"
        )
        return ret

    def get_sw_tag_class_status_by_id(self, d_id: int, tag_class_id: int):
        """Return the status of a specific software tag class by id from tags_info."""
        ret = self.__com_manager.get_boolean_property(
            d_id, "tags_info", f"sw_tag{tag_class_id}", "status"
        )
        return ret

    # def get_hw_tag_classes(self, d_id: int):
    #     hw_tags = dict()
    #     res = self.get_tags_info(d_id)
    #     for t in res["tags_info"]:
    #         if "hw" in t:
    #             hw_tags[t] = res["tags_info"][t]
    #     return hw_tags

    # def get_hw_tag_class(self, d_id: int, tag_class_id: int):
    #     ret = self.__com_manager.get_string_property(
    #         d_id, "tags_info", f"hw_tag{tag_class_id}"
    #     )
    #     return ret

    # def get_hw_tag_class_label_by_id(self, d_id: int, tag_class_id: int):
    #     ret = self.__com_manager.get_string_property(
    #         d_id, "tags_info", f"hw_tag{tag_class_id}", "label"
    #     )
    #     return ret

    # def get_hw_tag_class_enabled_by_id(self, d_id: int, tag_class_id: int):
    #     ret = self.__com_manager.get_boolean_property(
    #         d_id, "tags_info", f"hw_tag{tag_class_id}", "enabled"
    #     )
    #     return ret

    # def get_hw_tag_class_label(self, d_id: int, comp_name: str):
    #     ret = self.__com_manager.get_string_property(d_id, "tags_info", comp_name, "label")
    #     return ret

    # def get_hw_tag_class_enabled(self, d_id: int, comp_name: str):
    #     ret = self.__com_manager.get_boolean_property(d_id, "tags_info", comp_name, "enabled")
    #     return ret

    def send_command(self, d_id: int, message):
        """Send a raw command/message to the device via com_manager."""
        return self.__com_manager.send_command(d_id, message)

    def get_boolean_property(self, d_id: int, comp_name: str, prop_name: str):
        """Return a boolean property from the specified component."""
        return self.__com_manager.get_boolean_property(d_id, comp_name, prop_name)

    def get_integer_property(self, d_id: int, comp_name: str, prop_name: str):
        """Return an integer property from the specified component."""
        return self.__com_manager.get_integer_property(d_id, comp_name, prop_name)

    def get_string_property(self, d_id: int, comp_name: str, prop_name: str):
        """Return a string property from the specified component."""
        return self.__com_manager.get_string_property(d_id, comp_name, prop_name)

    def get_float_property(self, d_id: int, comp_name: str, prop_name: str):
        """Return a float property from the specified component."""
        return self.__com_manager.get_float_property(d_id, comp_name, prop_name)

    def set_acquisition_name(self, d_id: int, name: str):
        """Set the acquisition name in the acquisition_info component."""
        return self.__com_manager.set_property(d_id, name, self.acquisition_info_name, self.acquisition_name_field_name)

    def set_acquisition_description(self, d_id: int, description: str):
        """Set the acquisition description in the acquisition_info component."""
        return self.__com_manager.set_property(d_id, description, self.acquisition_info_name, self.acquisition_description_field_name)

    def set_acquisition_info(self, d_id: int, name: str, description: str):
        """Set both acquisition name and description in the acquisition_info component."""
        self.set_acquisition_name(d_id, name)
        return self.set_acquisition_description(d_id, description)

    def set_sensor_enable(self, d_id: int, new_status: bool, comp_name: str):
        """Set the enable status of a sensor component."""
        return self.__com_manager.set_property(d_id, new_status, comp_name, "enable")

    def set_sensor_odr(self, d_id: int, new_odr: float, comp_name: str):
        """Set the output data rate (ODR) of a sensor component."""
        return self.__com_manager.set_property(d_id, new_odr, comp_name, "odr")

    def set_sensor_fs(self, d_id: int, new_fs: float, comp_name: str):
        """Set the full scale (FS) of a sensor component."""
        return self.__com_manager.set_property(d_id, new_fs, comp_name, "fs")

    def set_sensor_aop(self, d_id: int, new_aop: float, comp_name: str):
        """Set the acoustic overload point (AOP) of a microphone-like sensor component."""
        return self.__com_manager.set_property(d_id, new_aop, comp_name, "aop")

    def set_sensor_samples_per_ts(self, d_id: int, new_spts: int, comp_name: str):
        """Set the samples per timestamp of a sensor component."""
        return self.__com_manager.set_property(d_id, new_spts, comp_name, "samples_per_ts", "val")

    def set_property(
        self, d_id: int, new_value, comp_name: str, prop_name: str, sub_prop_name: str = None
    ):
        """Generic method to set a property of a component."""
        return self.__com_manager.set_property(d_id, new_value, comp_name, prop_name, sub_prop_name)

    def set_sw_tag_class_enabled(self, d_id: int, tag_class_name: str, new_status: bool):
        """Set the enabled status of a software tag class."""
        return self.__com_manager.set_property(
            d_id, new_status, "tags_info", tag_class_name, "enabled"
        )

    def set_sw_tag_class_label(self, d_id: int, tag_class_name: str, new_label: str):
        """Set the label of a software tag class."""
        return self.__com_manager.set_property(
            d_id, new_label, "tags_info", tag_class_name, "label"
        )

    def set_sw_tag_on(self, d_id: int, tag_class_name: str):
        """Set a software tag on (status True)."""
        return self.__com_manager.set_property(d_id, True, "tags_info", tag_class_name, "status")

    def set_sw_tag_off(self, d_id: int, tag_class_name: str):
        """Set a software tag off (status False)."""
        return self.__com_manager.set_property(d_id, False, "tags_info", tag_class_name, "status")

    def set_sw_tag_class_enabled_by_id(self, d_id: int, tag_class_id: int, new_status: bool):
        """Set the enabled status of a software tag class by id."""
        return self.__com_manager.set_property(
            d_id, new_status, "tags_info", f"sw_tag{tag_class_id}", "enabled"
        )

    def set_sw_tag_class_label_by_id(self, d_id: int, tag_class_id: int, new_label: str):
        """Set the label of a software tag class by id."""
        return self.__com_manager.set_property(
            d_id, new_label, "tags_info", f"sw_tag{tag_class_id}", "label"
        )

    def set_sw_tag_on_by_id(self, d_id: int, tag_class_id: int):
        """Set a software tag on (status True) by id."""
        return self.__com_manager.set_property(
            d_id, True, "tags_info", f"sw_tag{tag_class_id}", "status"
        )

    def set_sw_tag_off_by_id(self, d_id: int, tag_class_id: int):
        """Set a software tag off (status False) by id."""
        return self.__com_manager.set_property(
            d_id, False, "tags_info", f"sw_tag{tag_class_id}", "status"
        )

    # def set_hw_tag_class_enabled(self, d_id:int, comp_name:str, new_status: bool):
    #     return self.__com_manager.set_property(
    #         d_id, new_status, "tags_info", comp_name, "enabled"
    #     )

    # def set_hw_tag_class_label(self, d_id:int, comp_name:str, new_label: str):
    #     return self.__com_manager.set_property(
    #         d_id, new_label, "tags_info", comp_name, "label"
    #     )

    # def set_hw_tag_class_enabled_by_id(self, d_id:int, tag_class_id:int, new_status: bool):
    #     return self.__com_manager.set_property(
    #         d_id, new_status, "tags_info", f"hw_tag{tag_class_id}", "enabled"
    #     )

    # def set_hw_tag_class_label_by_id(self, d_id:int, tag_class_id:int, new_label: str):
    #     return self.__com_manager.set_property(
    #         d_id, new_label, "tags_info", f"hw_tag{tag_class_id}", "label"
    #     )

    def get_sensor_data(self, d_id: int, comp_name: str, ss_id=None):
        """Pull sensor data samples from a component.

        Parameters:
            d_id: int
            comp_name: str
            ss_id: Not used! (it is kept for compatibility with older versions)

        Returns:
            Any: Sensor data payload as provided by com_manager.
        """
        _ = ss_id  # noqa: F841  # explicitly mark as intentionally unused
        return self.__com_manager.get_sensor_data(d_id, comp_name)

    def set_rtc_time(self, d_id: int, dtime=None):
        """Set device RTC time via log_controller.set_time command.

        Parameters:
            d_id: int
            dtime: Optional[str]
                If provided, must be formatted as "YYYYMMDD_HH_MM_SS".
                If None, current system time is used.

        Returns:
            Any: Response from device after sending command.

        Notes:
            - Time string must match firmware expectations.
            - This is typically called before starting a log session.
        """
        if dtime is None:
            now = datetime.now()
            rtc_time = now.strftime("%Y%m%d_%H_%M_%S")
        else:
            rtc_time = dtime
        message = PnPLCMDManager.create_command_cmd(
            "log_controller", "set_time", "datetime", rtc_time
        )
        return self.send_command(d_id, message)

    def start_log(
        self, d_id: int, interface: int = 1, acq_folder=None, sub_folder=True, save_files=True
    ):
        """Start an acquisition/logging session on the device.

        Parameters:
            d_id: int
                Device identifier.
            interface: int
                Interface selection for logging (implementation-specific; e.g., USB=1).
            acq_folder: Optional[str]
                Optional base folder override for this session.
            sub_folder: bool
                If True, create a timestamp subfolder under base folder.
                If False, use base folder directly.
            save_files: bool
                If True, create folders and allow JSON metadata saving.

        Behavior:
            - Sets device RTC time to current system time for traceability.
            - Updates base acquisition folder if acq_folder is provided.
            - Sets self.acquisition_folder based on sub_folder choice.
            - Creates the folder if save_files is True and it doesn't exist.
            - Delegates the actual start command to com_manager.start_log().

        Returns:
            Any: Result from com_manager.start_log(d_id, interface).

        Side effects:
            - May create directories on disk.
            - Updates internal flags: self.save_files and self.acquisition_folder.
        """
        self.set_rtc_time(d_id)
        log.info("Log Started")
        self.save_files = save_files
        if acq_folder is not None:
            self.update_base_acquisition_folder(acq_folder)
        if sub_folder == True:
            # Timestamped session folder for segregation
            self.acquisition_folder = os.path.join(
                self.__base_acquisition_folder, f"{datetime.today().strftime('%Y%m%d_%H_%M_%S')}"
            )
        else:
            self.acquisition_folder = self.__base_acquisition_folder

        if self.save_files and not os.path.exists(self.acquisition_folder):
            os.makedirs(self.acquisition_folder)
        return self.__com_manager.start_log(d_id, interface)

    def switch_bank(self, d_id: int):
        """Switch device log bank via log_controller.switch_bank.

        Useful for multi-bank logging firmwares to alternate storage.

        Returns:
            Any: Device response.
        """
        message = PnPLCMDManager.create_command_cmd("log_controller", "switch_bank")
        return self.send_command(d_id, message)

    def stop_log(self, d_id: int):
        """Stop the current logging session on the device.

        Returns:
            Any: com_manager.stop_log result.

        Notes:
            - Does not delete or finalize files; caller may save JSON afterward.
        """
        log.info("Log Stopped")
        return self.__com_manager.stop_log(d_id)

    def save_config(self, d_id):
        """Ask device to persist current configuration (firmware-dependent)."""
        message = PnPLCMDManager.create_command_cmd("log_controller", "save_config")
        return self.send_command(d_id, message)

    def save_json_device_file(self, d_id: int, out_acq_path=None):
        """Save device configuration JSON (device_config.json) into acquisition folder.

        Parameters:
            d_id: int
            out_acq_path: Optional[str]
                Override output folder; if provided and nonexistent, it is created.

        Behavior:
            - Reads device status via get_device_status(d_id).
            - Moves 'acquisition_info.uuid' into top-level 'uuid' and removes
                acquisition_info from components.
            - Writes JSON to 'device_config.json' in the chosen folder.

        Returns:
            bool: True on success.

        Raises:
            Any: Propagated exceptions on I/O or data formatting errors.

        Notes:
            - Only executes if self.save_files is True.
        """
        if self.save_files:
            json_save_path = self.acquisition_folder
            if out_acq_path is not None:
                if not os.path.exists(out_acq_path):
                    os.makedirs(out_acq_path)
                json_save_path = out_acq_path
            try:
                # Get the device status.
                device_status = self.get_device_status(d_id)
                if device_status is not None:
                    # Extract acquisition UUID and remove acquisition information component from the list.
                    for i, c in enumerate(device_status["devices"][0]["components"]):
                        if self.acquisition_info_name in c:
                            device_status["uuid"] = c[self.acquisition_info_name][self.acquisition_uuid_field_name]
                            device_status["devices"][0]["components"].pop(i)
                    device_status_fn = os.path.join(json_save_path, "device_config.json")
                    device_status_fd = open(device_status_fn, "w+", encoding="utf-8")
                    device_status_fd.write(json.dumps(device_status, indent=4))
                    device_status_fd.close()
                    log.info("device_config.json Configuration file correctly saved")
                    return True
            except:
                # Bubble up exception for caller to handle
                raise
        return False

    def save_json_acq_info_file(self, d_id: int, out_acq_path=None, manual_tags=None):
        """Save acquisition info JSON (acquisition_info.json) into acquisition folder.

        Parameters:
            d_id: int
            out_acq_path: Optional[str]
                Override output folder; created if missing.
            manual_tags: Optional[list|dict]
                If provided, replaces the 'tags' field before saving.

        Behavior:
            - Reads acquisition info via get_acquisition_info(d_id).
            - Optionally overwrites 'tags' field.
            - Writes JSON to 'acquisition_info.json' in the chosen folder.

        Returns:
            bool: True on success.

        Raises:
            Any: Propagated exceptions on I/O or data formatting errors.

        Notes:
            - Only executes if self.save_files is True.
        """
        if self.save_files:
            json_save_path = self.acquisition_folder
            if out_acq_path is not None:
                if not os.path.exists(out_acq_path):
                    os.makedirs(out_acq_path)
                json_save_path = out_acq_path
            try:
                res = self.get_acquisition_info(d_id)
                if res is not None:
                    acq_info_filename = os.path.join(json_save_path, "acquisition_info.json")
                    acq_info_file = open(acq_info_filename, "w+", encoding="utf-8")

                    if manual_tags is None:
                        acq_info_file.write(json.dumps(res[self.acquisition_info_name], indent=4))
                    else:
                        # Allow caller to inject tags post-stop or external inputs
                        res[self.acquisition_info_name]["tags"] = manual_tags
                        acq_info_file.write(json.dumps(res[self.acquisition_info_name], indent=4))
                    acq_info_file.close()
                    log.info("acquisition_info.json file correctly saved")
                    return True
            except:
                raise
        return False

    def update_device(self, d_id: int, device_json_file_path):  # device_config.json
        """Update device configuration from a JSON file using the DTDL device template.

        Parameters:
            d_id: int
            device_json_file_path: str
                Path to a device_config.json produced by save_json_device_file() or similar.

        Behavior:
            - If a DeviceTemplateManager is present, uses it directly.
            - Else, attempts to infer the proper DTDL model via DeviceCatalogManager
                using board_id and fw_id from device presentation and firmware info.
            - On success, loads the device template and performs update via com_manager.

        Side effects:
            - Logs status messages.
            - Sets internal __dt_manager when auto-loaded.

        Raises:
            WrongDeviceConfigFile: If the provided JSON does not match device template.
            MissingDeviceModelError: If no device template could be resolved
                (referenced but not imported here).

        Notes:
            - This relies on catalog availability and accurate firmware identification.
            - Caller should ensure device_json_file_path is correct and compatible.
        """
        try:
            if self.__dt_manager is not None:
                return self.__com_manager.update_device(
                    d_id, device_json_file_path, self.__dt_manager.get_components()
                )
            print("HSD_link does not have an associated Device Template")
            pres_res = self.get_device_presentation_string(d_id)
            if pres_res is not None:
                board_id = hex(pres_res["board_id"])
                fw_id = hex(pres_res["fw_id"])

                dev_template_json = DeviceCatalogManager.query_dtdl_model(board_id, fw_id)
                fw_name = self.get_firmware_info(d_id).get("fw_name")
                if fw_name is not None:
                    if isinstance(dev_template_json, list):
                        # In case of multiple possible templates, print candidates to aid selection
                        for dt in dev_template_json:
                            print(dt)

                self.__dt_manager = DeviceTemplateManager(dev_template_json)
                self.__com_manager.update_device(
                    d_id, device_json_file_path, self.__dt_manager.get_components()
                )
                log.info("Device Template automatically loaded")
            else:
                log.error("No Device Template loaded")
                raise MissingDeviceModelError
        except WrongDeviceConfigFile:
            # Propagate specific error to caller for handling
            raise

    def __read_text_file(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def __build_ucf_payload(self, file_content: str):
        lines = [line.replace(" ", "") for line in file_content.splitlines()]
        lines = list(filter(None, lines))

        ucf_buffer = ""
        for line in lines:
            if line == "" or line.startswith("--"):
                continue
            if "WAIT" in line:
                wtime = line[4:]
                time_digit = len(wtime)
                missing_digit = 3 - time_digit
                wtime = "0" * missing_digit + wtime
                ucf_buffer += "W" + wtime
            else:
                ucf_buffer += line[2:]
        return ucf_buffer

    def __load_reg_config_json(self, json_file_path: str, core_name: str):
        try:
            with open(json_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f'Error parsing {core_name} JSON file ["{json_file_path}"]: {exc}'
            ) from exc

    def __get_first_reg_config_sensor(self, source_payload: dict, core_name: str):
        if not isinstance(source_payload, dict):
            raise ValueError(f"{core_name} JSON payload must be a JSON object")

        raw_sensors = source_payload.get("sensors", [])
        if not isinstance(raw_sensors, list) or len(raw_sensors) == 0:
            raise ValueError(f"{core_name} JSON payload must contain at least one sensor")

        sensor = raw_sensors[0]
        if len(raw_sensors) > 1:
            log.warning(
                "%s JSON payload contains multiple sensors, but only the first one will be"
                " processed by the current firmware implementation",
                core_name,
            )
        return sensor

    def __build_reg_config_payload(self, source_payload: dict, core_name: str):
        sensor = self.__get_first_reg_config_sensor(source_payload, core_name)
        configuration = sensor.get("configuration", [])
        if not isinstance(configuration, list):
            raise ValueError(f"{core_name} JSON payload sensor configuration must be a list")

        def normalize_hex_pair(value, field_name):
            if not isinstance(value, str):
                raise ValueError(f"{core_name} JSON payload {field_name} must be a string")

            normalized = value.strip()
            if normalized.lower().startswith("0x"):
                normalized = normalized[2:]

            if normalized == "":
                raise ValueError(f"{core_name} JSON payload {field_name} cannot be empty")

            try:
                int(normalized, 16)
            except ValueError as exc:
                raise ValueError(
                    f"{core_name} JSON payload {field_name} must contain a valid hexadecimal value"
                ) from exc

            return normalized.upper()

        def normalize_delay(entry):
            delay_value = None
            for key in ["delay", "data"]:
                if entry.get(key) is not None:
                    delay_value = entry.get(key)
                    break

            if delay_value is None:
                raise ValueError(
                    f"{core_name} JSON payload delay entries must contain one of: time, delay, value, data"
                )

            try:
                delay_int = int(str(delay_value).strip(), 0)
            except ValueError as exc:
                raise ValueError(f"{core_name} JSON payload delay value must be an integer") from exc

            if delay_int < 0:
                raise ValueError(f"{core_name} JSON payload delay value cannot be negative")

            return f"W{delay_int:03d}"

        ucf_buffer = ""
        for entry in configuration:
            if not isinstance(entry, dict):
                raise ValueError(
                    f"{core_name} JSON payload configuration entries must be JSON objects"
                )

            entry_type = entry.get("type")
            if entry_type == "write":
                address = normalize_hex_pair(entry.get("address"), "address")
                data = normalize_hex_pair(entry.get("data"), "data")
                ucf_buffer += address + data
            elif entry_type == "delay":
                ucf_buffer += normalize_delay(entry)

        return ucf_buffer

    def __extract_unified_outputs(self, source_payload: dict, core_name: str, require_outputs=False):
        sensor = self.__get_first_reg_config_sensor(source_payload, core_name)
        outputs = sensor.get("outputs")
        if outputs is None:
            if require_outputs:
                raise ValueError(
                    f"{core_name} JSON payload must contain a non-empty outputs section"
                )
            return []

        if not isinstance(outputs, list):
            raise ValueError(f"{core_name} JSON payload outputs must be a list")

        if require_outputs and len(outputs) == 0:
            raise ValueError(f"{core_name} JSON payload must contain a non-empty outputs section")

        normalized_outputs = []
        for output in outputs:
            if not isinstance(output, dict):
                raise ValueError(f"{core_name} JSON payload outputs entries must be JSON objects")

            name = output.get("name")
            output_type = output.get("type")
            if not name or not output_type:
                raise ValueError(
                    f"{core_name} JSON payload outputs entries must contain both name and type"
                )

            normalized_outputs.append({"name": str(name), "type": str(output_type)})

        return normalized_outputs

    def __build_legacy_ispu_output_payload(self, source_payload: dict):
        return json.dumps({"output": self.__extract_unified_outputs(source_payload, "ISPU", True)})

    def upload_mlc_ucf_file(self, d_id: int, comp_name: str, ucf_file_path):
        """Upload an MLC UCF file to a component.

        TODO: make this old function able also to upload JSON configuration files for 
            MLC components, by adding a check on the file extension and parsing the file content accordingly.
            This to not break the current implementation that is used in production, but to extend it with
            the new feature of uploading JSON files, that is more user-friendly than UCF files.
            A new function upload_mlc_json_file is added to provide a more user-friendly way to upload JSON files,
            while keeping the old function for backward compatibility.

        Parameters:
            d_id: int
            comp_name: str
                Target component name (e.g., 'ism330dhcx_mlc').
            ucf_file_path: str
                Path to .ucf file. File may include WAIT directives and comments.

        Behavior:
            - Reads UCF file, strips spaces/newlines, filters empty lines.
            - Handles WAIT directives by converting to 'Wxxx' format with zero-padding.
            - Concatenates register writes and waits into a single buffer string.
            - Sends 'load_file' command with {size, data} payload to the component.

        Returns:
            Any: Device response or None if failed.

        Notes:
            - Uses PnPL command comp_name.load_file ucf_data to transfer content.
            - Prints success message and logs errors.
        """
        ucf_buffer = self.__build_ucf_payload(self.__read_text_file(ucf_file_path))
        ucf_size = len(ucf_buffer)
        ucf = {"size": ucf_size, "data": ucf_buffer}
        message = PnPLCMDManager.create_command_cmd(comp_name, "load_file", "ucf_data", ucf)
        res = self.send_command(d_id, message)
        if res is not None:
            print(f'UCF file ["{ucf_file_path}"] loaded correctly in {comp_name} Component')
        else:
            log.error(f'Error loading UCF file ["{ucf_file_path}"] in {comp_name} Component')
        return res

    def __build_mlc_json_payload(self, source_payload: dict):
        return self.__build_reg_config_payload(source_payload, "MLC")
    
    def upload_mlc_json_file(self, d_id: int, comp_name: str, json_file_path: str):
        """Upload an MLC JSON file to a component.

        Parameters:
            d_id: int
            comp_name: str
                Target component name (e.g., 'ism330dhcx_mlc').
            json_file_path: str
                Path to .json file containing MLC configuration.

        Behavior:
            - Reads the JSON file as structured JSON data.
            - Converts the configuration entries into the UCF-equivalent stream.
            - Sends 'load_file' with the generated {size, data} payload to the component.

        Returns:
            Any: Device response or None if failed.

        Notes:
            - 'write' entries are encoded as address+data with the '0x' prefix removed.
            - 'delay' entries are encoded with the same 'Wxxx' mechanism used by UCF WAIT directives.
            - Prints success message and logs errors.
        """
        try:
            source_payload = self.__load_reg_config_json(json_file_path, "MLC")
        except ValueError as exc:
            log.error(exc)
            return None

        try:
            ucf_buffer = self.__build_mlc_json_payload(source_payload)
        except ValueError as exc:
            log.error(f'Error building MLC UCF payload from JSON file ["{json_file_path}"]: {exc}')
            return None

        mlc_ucf = {"size": len(ucf_buffer), "data": ucf_buffer}
        message = PnPLCMDManager.create_command_cmd(comp_name, "load_file", "ucf_data", mlc_ucf)
        res = self.send_command(d_id, message)
        if res is not None:
            print(f'MLC JSON file ["{json_file_path}"] loaded correctly in {comp_name} Component')
        else:
            log.error(f'Error loading MLC JSON file ["{json_file_path}"] in {comp_name} Component')
        return res

    def upload_ispu_ucf_file(
        self, d_id: int, comp_name: str, ucf_file_path, output_json_file_path=None
    ):
        """Upload an ISPU UCF file to a component with optional output JSON (placeholder).

        Parameters:
            d_id: int
            comp_name: str
            ucf_file_path: str
            output_json_file_path: Optional[str]
                Placeholder for future output format support (currently unused).

        Behavior:
            - Same parsing logic as upload_mlc_ucf_file (WAIT handling, stripping comments).
            - Constructs a payload with ucf_size/ucf_data and placeholder output fields.
            - Sends 'load_file' command with 'files' payload.

        Returns:
            Any: Device response or None if failed.

        Notes:
            - Output JSON support is marked for future release in comment.
        """
        ucf_buffer = self.__build_ucf_payload(self.__read_text_file(ucf_file_path))
        ucf_size = len(ucf_buffer)
        out_json_size = 0
        out_json_data = ""
        if output_json_file_path is not None:
            out_json_data = self.__read_text_file(output_json_file_path)
            out_json_size = len(out_json_data)
        cmd_value = {
            "ucf_size": ucf_size,
            "ucf_data": ucf_buffer,
            "output_size": out_json_size,
            "output_data": out_json_data,
        }
        message = PnPLCMDManager.create_command_cmd(comp_name, "load_file", "files", cmd_value)
        res = self.send_command(d_id, message)
        if res is not None:
            print(f'UCF file ["{ucf_file_path}"] loaded correctly in {comp_name} Component')
        else:
            log.error(f'Error loading UCF file ["{ucf_file_path}"] in {comp_name} Component')
        return res
    
    def upload_ispu_json_file(
        self, d_id: int, comp_name: str, json_file_path: str, output_json_file_path=None
    ):
        """Upload an ISPU JSON file to a component with optional output JSON.

        Parameters:
            d_id: int
            comp_name: str
            json_file_path: str
            output_json_file_path: Optional[str]

        Behavior:
            - Reads JSON file content as a string.
            - Constructs a payload with json_size/json_data and optional output JSON.
            - Sends 'load_file' command with 'files' payload.

        Returns:
            Any: Device response or None if failed.
        """
        try:
            source_payload = self.__load_reg_config_json(json_file_path, "ISPU")
            ucf_buffer = self.__build_reg_config_payload(source_payload, "ISPU")
        except ValueError as exc:
            log.error(exc)
            return None

        try:
            if output_json_file_path is not None:
                out_json_data = self.__read_text_file(output_json_file_path)
            else:
                out_json_data = self.__build_legacy_ispu_output_payload(source_payload)
        except ValueError as exc:
            log.error(f'Error building ISPU output payload from JSON file ["{json_file_path}"]: {exc}')
            return None

        out_json_size = len(out_json_data)
        cmd_value = {
            "ucf_size": len(ucf_buffer),
            "ucf_data": ucf_buffer,
            "output_size": out_json_size,
            "output_data": out_json_data,
        }
        message = PnPLCMDManager.create_command_cmd(comp_name, "load_file", "files", cmd_value)
        res = self.send_command(d_id, message)
        if res is not None:
            print(f'JSON file ["{json_file_path}"] loaded correctly in {comp_name} Component')
        else:
            log.error(f'Error loading JSON file ["{json_file_path}"] in {comp_name} Component')
        return res


class HSDLink_v2_Serial(HSDLink_v2):
    """Serial-specific link variant that overrides a few behaviors.

    Differences vs HSDLink_v2:
        - Uses PnPLSTSRL communication manager.
        - send_command delegates to send_pnpl_msg.
        - stop_log flushes serial buffer after a short delay.
        - Exposes additional serial-oriented helpers (get_serial_data, flush, etc.).
    """

    def __init__(
        self,
        dev_com_type: str = "st_serial_datalog",
        acquisition_folder=None,
        plug_callback=None,
        unplug_callback=None,
        update_catalog=True,
    ):
        """Constructs the base class with dev_com_type pre-set to 'st_serial_datalog'."""
        super().__init__(
            dev_com_type, acquisition_folder, plug_callback, unplug_callback, update_catalog
        )
        self.__com_manager = self.get_com_manager()
        self.is_datalog1 = False
        self.is_vdatalog = True

    # @override (same body; kept for clarity of behavior)
    def set_rtc_time(self, d_id: int, dtime=None):
        """Set device RTC time via log_controller.set_time (serial variant)."""
        if dtime is None:
            now = datetime.now()
            rtc_time = now.strftime("%Y%m%d_%H_%M_%S")
        else:
            rtc_time = dtime
        message = PnPLCMDManager.create_command_cmd(
            "log_controller", "set_time", "datetime", rtc_time if dtime is None else dtime
        )
        return self.send_command(d_id, message)

    def get_devices(self):
        """Return devices descriptor from serial manager."""
        return self.__com_manager.get_devices()

    def send_command(self, d_id: int, message):
        """Send PnPL message over serial (serial manager API differs)."""
        return self.__com_manager.send_pnpl_msg(message)

    def stop_log(self, d_id):
        """Stop logging, wait briefly, then flush serial buffer.

        Returns:
            Any: Result from base stop_log.

        Notes:
            - Serial transports may require a short delay and flush to ensure
                trailing frames are consumed and buffers are cleared.
        """
        ret = super().stop_log(d_id)
        time.sleep(1)
        self.flush()
        return ret

    def get_serial_data(self):
        """Fetch raw serial data buffer (if supported)."""
        try:
            serial_data = self.__com_manager.get_serial_data()
        except PnPLSTSRLComManagerError as e:
            log.error("Error getting serial data: {}".format(e))
            # Wrap lower-level com manager error into an HSDLink_v2-specific error
            raise HSDLinkV2SerialError("HSDLink_v2: error getting serial data") from e
        return serial_data

    def flush(self):
        """Flush serial buffers to discard pending data."""
        return self.__com_manager.flush()

    def get_components_count(self, d_id: int):
        """Return total components count for a device (serial variant)."""
        return self.__com_manager.get_components_count(d_id)

    def get_actuator_components_count(self, d_id: int, only_active: bool = False):
        """Return count of actuator components (optionally only active)."""
        return self.__com_manager.get_actuator_components_count(d_id, only_active)

    def get_components_names(self, d_id: int):
        """Return names of all components for a device."""
        return self.__com_manager.get_components_names(d_id)

    def get_actuator_components_names(self, d_id: int, only_active: bool = False):
        """Return actuator component names (optionally only active)."""
        return self.__com_manager.get_actuator_components_names(d_id, only_active)

    def get_actuators_status(self, d_id: int, only_active: bool = False):
        """Return actuator components status (optionally only active)."""
        return self.__com_manager.get_actuator_components_status(d_id, only_active)

    # The following methods allow customization of component and field names for compatibility with different firmware versions or schemas.

    def set_custom_device_info_component_name(self, name: str):
        """Set the name of the device info component for compatibility with different firmware versions."""
        self.device_info_name = name

    def set_custom_firmware_info_component_name(self, name: str):
        """Set the name of the firmware info component for compatibility with different firmware versions."""
        self.firmware_info_name = name

    def set_custom_acquisition_info_component_name(self, name: str):
        """Set the name of the acquisition info component for compatibility with different firmware versions."""
        self.acquisition_info_name = name

    def set_custom_acquisition_uuid_field_name(self, name: str):
        """Set the field name for acquisition UUID in the acquisition info component."""
        self.acquisition_uuid_field_name = name

    def set_custom_acquisition_name_field_name(self, name: str):
        """Set the field name for acquisition name in the acquisition info component."""
        self.acquisition_name_field_name = name

    def set_custom_acquisition_description_field_name(self, name: str):
        """Set the field name for acquisition description in the acquisition info component."""
        self.acquisition_description_field_name = name

    def set_custom_measured_odr_field_name(self, name: str):
        """Set the field name for measured ODR in the acquisition info component."""
        self.measured_odr_field_name = name

    def set_custom_dimensions_field_name(self, name: str):
        """Set the field name for dimensions in the acquisition info component."""
        self.dimensions_field_name = name

    def set_custom_initial_offset_field_name(self, name: str):
        """Set the field name for initial offset in the acquisition info component."""
        self.initial_offset_field_name = name

    def set_custom_annotation_field_name(self, name: str):
        """Set the field name for annotation in the acquisition info component."""
        self.annotation_field_name = name

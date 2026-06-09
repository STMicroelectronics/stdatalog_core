
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
PnPL over HSD Command Manager.

This module exposes `PnPLHSD_CommandManager`, a high-level helper to communicate with devices
using the PnPL command set over the HSDatalog transport (via the `HSD_Dll` shared library).

Overview:
- Loads and opens the HSD communication engine (DLL/so) on initialization.
- Builds PnPL commands using `PnPLCMDManager` and sends them through `HSD_Dll`.
- Returns device responses as Python types (e.g., dict via JSON parsing).
- Provides convenience methods to query device info/status, enumerate components, and set values.
- Supports starting/stopping logging through the HSD engine.

Key Classes:
- `PnPLHSD_CommandManager`: Main interface on top of `HSD_Dll` and PnPL commands.
- `PnPLHSD_Creator`: Factory to create a command manager with the default command set.

Workflow:
1. Construct `PnPLHSD_CommandManager` (optionally passing hotplug callbacks;
    currently not supported).
2. Use methods like `get_device_status`, `get_component_status`, or `set_property`.
3. Use `start_log` / `stop_log` to control data logging.
4. Call `close()` to release the communication engine.

Errors and Exceptions:
- `CommunicationEngineOpenError`: Raised when the HSD engine cannot be opened.
- `EmptyCommandResponse`: Raised when a command returns no data or parsing fails.
- `PnPLCommandError`, `SETCommandError`, `PnPLSETDeviceStatusCommandError`:
    Raised on command/protocol-level failures.
- `WrongDeviceConfigFile`: Raised if a provided config does not match the connected device.

Usage Example:
        >>> from stdatalog_core.HSD_link.communication.PnPL_HSD.PnPLHSD_com_manager import (
        ...     PnPLHSD_CommandManager,
        ... )
        >>> cm = PnPLHSD_CommandManager(cmd_set="pnpl")
        >>> cm.get_version()
        'x.y.z'
        >>> status = cm.get_device_status(0)
        >>> cm.close()
"""

import json

from stdatalog_pnpl.DTDL import device_template_model as DTM
from stdatalog_pnpl.PnPLCmd import PnPLCMDManager

import stdatalog_core.HSD_utils.logger as logger
from stdatalog_core.HSD_utils.exceptions import (
    CommunicationEngineOpenError,
    EmptyCommandResponse,
    PnPLCommandError,
    SETCommandError,
    PnPLSETDeviceStatusCommandError,
    WrongDeviceConfigFile,
)
from stdatalog_core.HSD_link.communication.PnPL_HSD.hsd_dll import HSD_Dll

log = logger.get_logger(__name__)

class PnPLHSD_CommandManager:
    """
    PnPL HSD Command Manager.
    This class manages communication with devices using the PnPL protocol over HSDatalog.
    Thanks to HSDatalog_v2 DLL/so for the low level communication handling.
    uses PnPLCMDManager for command creation, and uses HSD_Dll for communication.

    Methods:
        - __init__(cmd_set, plug_callback=None, unplug_callback=None):
          Initialize the command manager.
        - open(): Open the communication engine.
        - close(): Close the communication engine.
        - get_nof_devices(): Get the number of connected devices.
        - get_version(): Get the version of the communication engine.
        - get_device_info(d_id): Get device information.
        - send_command(d_id, message): Send a command to a device.
        - send_data(): Not implemented function to send data.
        - get_sensor_data(d_id, comp_name): Get sensor data from a device.
        - get_cmd_set_presentation_string(): Get the command set presentation string.
        - get_device_presentation_string(d_id): Get the device presentation string.
        - get_device_identity(d_id): Get the device identity string.
        - get_device_alias(d_id): Get the device alias.
        - set_device_alias(d_id, alias): Set the device alias.
        - start_log(d_id, interface=1): Start logging on a device.
        - stop_log(d_id): Stop logging on a device.
        - get_devices(): Get the list of connected devices.
        - get_device_status(d_id): Get the status of a device.
        - get_device(d_id): Get the device model from its status.
                - set_device_status(d_id, device_status_json_file_path):
                    Set the device status using a JSON file.
                - get_sensor_components_status(d_id, type_filter, only_active):
                    Get sensor component statuses.
        - get_algorithm_components_status(d_id, only_active): Get algorithm component statuses.
        - get_component_status(d_id, component_name): Get the status of a specific component.
        - get_components_count(d_id): Get the number of components on a device.
                - get_sensor_components_count(d_id, only_active):
                    Get the number of sensor components on a device.
                - get_algorithm_components_count(d_id, only_active):
                    Get the number of algorithm components on a device.
                - get_sensor_components_names(d_id, type_filter, only_active):
                    Get names of sensor components.
        - get_algorithm_components_names(d_id, only_active): Get names of algorithm components.
                - get_boolean_property(d_id, comp_name, prop_name, sub_prop_name=None):
                    Get a boolean property value.
    """
    def __init__(self, cmd_set, plug_callback = None, unplug_callback = None):
        """
        Initialize the PnPL HSD Command Manager.
        :param cmd_set: Command set to use. (fixed in PnPLHSD_Creator to be pnpl commands)
            -- it is kept here for API consistency with other CommandManagers --.
        :param plug_callback: Callback function for USB plug event (not supported yet).
        :param unplug_callback: Callback function for USB unplug event (not supported yet).
        :return: None
        """
        _ = cmd_set # to avoid unused parameter warning
        self.cmd_set = None
        self.hsd_dll = HSD_Dll()

        if plug_callback is not None and unplug_callback is not None:
            log.warning(
                "Hotplug event management is not supported yet. "
                "your plug_callback and unplug_callback will be ignored."
            )
            self.hsd_dll.hs_datalog_register_usb_hotplug_callback(plug_callback, unplug_callback)
        # time.sleep(1)
        if not self.hsd_dll.hs_datalog_open():
            log.error("Error in Communication Engine opening (libhs_datalog_v2 DLL/so)")
            raise CommunicationEngineOpenError
        else:
            log.info("Communication Engine UP (libhs_datalog_v2 DLL/so)")

    def open(self):
        """
        Open the communication engine.
        :return: True if the communication engine was opened successfully, False otherwise.
        """
        return self.hsd_dll.hs_datalog_open()

    def close(self):
        """
        Close the communication engine.
        :return: True if the communication engine was closed successfully, False otherwise.
        """
        return self.hsd_dll.hs_datalog_close()

    def get_nof_devices(self):
        """
        Get the number of connected devices from the communication engine.
        :return: Number of connected devices.
        """
        res = self.hsd_dll.hs_datalog_get_device_number()
        if res[0]:
            return res[1]
        log.error("Empty response from get_nof_devices(...).")
        raise EmptyCommandResponse("get_nof_devices")

    def get_version(self):
        """
        Get the version of the communication engine.
        :return: Version string.
        """
        res = self.hsd_dll.hs_datalog_get_version()
        return res[0]

    def get_device_info(self, d_id:int):
        """
        Get the device information from the device.
        :param d_id: Device ID to get the device information for.
        :return: Device information as a dictionary.
        """
        return self.get_component_status(d_id, "firmware_info")

    def __send_message(self, d_id: int, message):
        """
        Send a message to the device. This function sends the message and waits for a response.
        Note: This function is synchronous and will block until a response is received.
        :param d_id: Device ID to send the message to.
        :param message: Message to send.
        :return: Response message from the device.
        """
        res = self.hsd_dll.hs_datalog_send_message(d_id,message,len(message))
        if res[0]:
            return res[2]
        return None

    def send_command(self, d_id: int, message):
        """
        Send a command message to the device. This function sends the command and
        waits for a response.
        Note: This function is synchronous and will block until a response is received.
        :param d_id: Device ID to send the command to.
        :param message: Command message to send.
        :return: Response message from the device.
        """
        return self.__send_message(d_id,message)

    def send_data(self):
        """
        Not implemented function to send data to the device.
        :return: None
        """
        log.error("Not implemented function")
        raise EmptyCommandResponse("send_data")

    def get_sensor_data(self, d_id: int, comp_name:str):
        """
        Get sensor data from the device.
        :param d_id: Device ID to get the sensor data for.
        :param comp_name: Component name to get the sensor data for.
        :return: Sensor data as a list [data_size, data_bytes] or None if no data is available.
        """
        size = self.hsd_dll.hs_datalog_get_available_data_size(d_id, comp_name)
        if size[1] > 0:
            data = self.hsd_dll.hs_datalog_get_data(d_id, comp_name, size[1])
            if data[0]:
                return [size[1], data[1]]
        return None

    def get_cmd_set_presentation_string(self):
        """
        Get the command set presentation string.
        :return: Command set presentation string.
        """
        return "Vespucci PnPL Commands"

    def get_device_presentation_string(self, d_id: int):
        """
        Get the device presentation string. It contains information about the board
        and firmware IDs.
        The presentation string is returned as a dictionary with 'board_id' and 'fw_id' keys.
        Note: This function returns the same information as get_device_identity.
        :param d_id: Device ID to get the presentation for.
        :return: Presentation string.
        """
        if d_id is not None:
            res = self.hsd_dll.hs_datalog_get_presentation(d_id)
            if res[0]:
                return {"board_id" : res[1], "fw_id": res[2]}
            log.error(f"Empty response from get_device_presentation_string(d_id={d_id})")
            raise EmptyCommandResponse("get_device_presentation_string")
        log.error("Missing Device id")
        raise PnPLCommandError("get_device_presentation_string")

    def get_device_identity(self, d_id: int):
        """
        Get the device identity string. It contains information about the board and
        firmware IDs.
        Identity string is returned as a dictionary with 'board_id' and 'fw_id' keys.
        Note: This function returns the same information as get_device_presentation_string.
        :param d_id: Device ID to get the identity for.
        :return: Identity string.
        """
        if d_id is not None:
            res = self.hsd_dll.hs_datalog_get_identity(d_id)
            if res[0]:
                return {"board_id" : res[1], "fw_id": res[2]}
            log.error(f"Empty response from get_device_identity(d_id={d_id})")
            raise EmptyCommandResponse("get_device_identity")
        log.error("Missing Device id")
        raise PnPLCommandError("get_device_identity")

    def get_device_alias(self, d_id: int):
        """
        Get the device alias. Alias is a user-defined string that can be used to
        identify the device.
        :param d_id: Device ID to get the alias for.
        :return: Alias string.
        """
        return self.get_device_info(d_id)["firmware_info"]["alias"]

    def set_device_alias(self, d_id: int, alias: str):
        """
        Set the device alias. Alias is a user-defined string that can be used to
        identify the device.
        :param d_id: Device ID to set the alias for.
        :param alias: Alias string to set.
        :return: True if alias was set successfully, False otherwise.
        """
        res = self.hsd_dll.hs_datalog_set_device_alias(d_id, alias)
        if res is not None:
            log.info(f"Device Alias set to {alias}")
            return True
        log.error("Error Device Alias update.")
        raise SETCommandError("set_device_alias")

    def start_log(self,d_id: int, interface: int = 1):
        """
        Start logging on the device.
        :param d_id: Device ID to start logging for.
        :param interface: Interface to use for logging (1 for USB, 2 for BLE).
        :return: True if logging was started successfully, False otherwise.
        """
        return self.hsd_dll.hs_datalog_start_log(d_id, interface)

    def stop_log(self,d_id: int):
        """
        Stop logging on the device.
        :param d_id: Device ID to stop logging for.
        :return: True if logging was stopped successfully, False otherwise.
        """
        return self.hsd_dll.hs_datalog_stop_log(d_id)

    def get_devices(self):
        """
        Get the list of devices from the communication engine.
        :return: List of device models as DTM.Device objects.
        """
        nof_devices = self.get_nof_devices()
        if nof_devices is not None:
            dev_list=[]
            for i in range(0,nof_devices):
                device = self.get_device_status(i)
                dev_list.append(device)
            return dev_list
        return None

    def get_device_status(self, d_id: int):
        """
        Get the device status from the device.
        :param d_id: Device ID to get the device status for.
        :return: Device status as a dictionary.
        """
        res = self.hsd_dll.hs_datalog_get_device_status(d_id)
        if res[0]:
            try:
                device_dict = json.loads(res[1])
                return device_dict
            except json.decoder.JSONDecodeError as exc:
                log.error(f"Device Status[d_id:{d_id}] parsing error.")
                raise EmptyCommandResponse("get_device_status") from exc
        log.error(f"No Device Status[d_id:{d_id}] returned.")
        raise EmptyCommandResponse("get_device_status")

    def get_device(self, d_id: int):
        """
        Get the device model from the device status.
        :param d_id: Device ID to get the device model for.
        :return: Device model as a DTM.Device object.
        """
        return self.get_device_status(d_id)

    def set_device_status(self, d_id: int, device_status_json_file_path):
        """
        Set the device status on the device using a JSON file.
        :param d_id: Device ID to set the device status for.
        :param device_status_json_file_path: Path to the JSON file containing the device status.
        :return: None
        """
        with open(device_status_json_file_path, encoding="utf-8") as f:
            device_json = json.load(f)
            f.close()
        res = self.hsd_dll.hs_datalog_set_device_status(d_id, device_json)
        if res:
            log.info(f"Device Status [{d_id}] correctly updated")
        log.error("Error in Device Status update.")
        raise PnPLSETDeviceStatusCommandError("set_device_status")

    def __get_components_status(self, d_id: int, c_type:int):
        """
        Get the status of components on the device filtered by component type.
        :param d_id: Device ID to get the component status for.
        :param c_type: Component type to filter by (0 for sensors, 1 for algorithms,
            2 for others, 3 for actuators).
        :return: Dictionary of component statuses filtered by type.
        """
        res = dict()
        devices = self.get_device_status(d_id)
        device_status = devices["devices"][0]
        for comp in device_status["components"]:
            comp_name = list(comp.keys())[0]
            comp_status = comp[comp_name]
            if "c_type" in comp_status and comp_status["c_type"] == c_type:
                res[comp_name] = comp_status
        return res

    def get_sensor_components_status(self, d_id: int, type_filter, only_active):
        """
        Get the status of sensor components on the device.
        The status can be filtered by sensor type and active status.
        This function retrieves all sensor components and then filters them based on
        the provided criteria.
        :param d_id: Device ID to get the sensor component status for.
        :param type_filter: Filter by sensor type (e.g., 'accel', 'gyro'). If empty,
            no filtering is applied.
        :param only_active: If True, return only active sensor components; otherwise, return all.
        :return: Dictionary of sensor component statuses.
        """
        disabled_sensors_names = []
        sensors_c = self.__get_components_status(d_id,0)
        if type_filter == "":
            if only_active == True:
                for sc, status in sensors_c.items():
                    if not status.get("enable", False):
                        disabled_sensors_names.append(sc)
        else:
            if only_active == True:
                for sc, status in sensors_c.items():
                    sensor_type = sc.lower().split("_")[-1]
                    if (not status.get("enable", False)) or (sensor_type != type_filter.lower()):
                        disabled_sensors_names.append(sc)
            else:
                for sc in sensors_c:
                    sensor_type = sc.lower().split("_")[-1]
                    if sensor_type != type_filter.lower():
                        disabled_sensors_names.append(sc)
        if len(disabled_sensors_names) > 0:
            for dsn in disabled_sensors_names:
                del sensors_c[dsn]
        return sensors_c

    def get_algorithm_components_status(self, d_id: int, only_active: bool):
        """
        Get the status of algorithm components on the device.
        :param d_id: Device ID to get the algorithm component status for.
        :param only_active: If True, return only active algorithm components; otherwise, return all.
        :return: Dictionary of algorithm component statuses.
        """
        algo_c = self.__get_components_status(d_id,1)
        if only_active == True:
            disabled_algo_names = []
            for name, status in algo_c.items():
                if not status.get("enable", False):
                    disabled_algo_names.append(name)
            if len(disabled_algo_names) > 0:
                for dsn in disabled_algo_names:
                    del algo_c[dsn]
        return algo_c

    def get_component_status(self, d_id:int, component_name:str):
        """
        Get the status of a specific component on the device.
        :param d_id: Device ID to get the component status for.
        :param component_name: Name of the component to get the status for.
        :return: Component status as a dictionary.
        """
        res = self.hsd_dll.hs_datalog_get_component_status(d_id, component_name)
        if res[0] and len(res[1]) > 0:
            try:
                sensor_dict = json.loads(res[1])
                return sensor_dict
            except Exception as exc:
                log.error(f"Component Status[d_id={d_id},{component_name}] parsing error.")
                raise EmptyCommandResponse("get_component_status") from exc
        log.error(f"No Component Status[d_id={d_id},{component_name}] returned.")
        raise EmptyCommandResponse("get_component_status")

    def get_components_count(self, d_id: int):
        """
        Get the number of components on the device.
        :param d_id: Device ID to get the component count for.
        :return: Number of components.
        """
        res = self.hsd_dll.hs_datalog_get_components_number(d_id)
        if res[0]:
            return res[1]
        log.error(f"Empty response from get_components_count(d_id={d_id})")
        raise EmptyCommandResponse("get_components_count")

    def get_sensor_components_count(self, d_id: int, only_active: bool):
        """
        Get the number of sensor components on the device.
        :param d_id: Device ID to get the sensor component count for.
        :param only_active: If True, count only active sensor components; otherwise, count all.
        :return: Number of sensor components.
        """
        res = self.hsd_dll.hs_datalog_get_sensor_components_number(d_id, only_active)
        if res[0]:
            return res[1]
        log.error(f"Empty response from get_sensor_components_count(d_id={d_id})")
        raise EmptyCommandResponse("get_sensor_components_count")

    def get_algorithm_components_count(self, d_id: int, only_active: bool):
        """
        Get the number of algorithm components on the device.
        :param d_id: Device ID to get the algorithm component count for.
        :param only_active: If True, count only active algorithm components; otherwise, count all.
        :return: Number of algorithm components.
        """
        res = self.hsd_dll.hs_datalog_get_algorithm_components_number(d_id, only_active)
        if res[0]:
            return res[1]
        log.error(f"Empty response from get_algorithm_components_count(d_id={d_id})")
        raise EmptyCommandResponse("get_algorithm_components_count")

    def get_sensor_components_names(self, d_id: int, type_filter: str, only_active: bool):
        """
        Get the names of sensor components on the device.
        :param d_id: Device ID to get the sensor component names for.
        :param type_filter: Filter by sensor type (e.g., 'accel', 'gyro'). If empty,
            no filtering is applied.
        :param only_active: If True, return only active sensor components; otherwise, return all.
        :return: List of sensor component names.
        """
        sensor_names = []
        res = self.get_sensor_components_status(d_id, type_filter, only_active)
        if res is not None:
            for name, status in res.items():
                if not only_active or status.get("enable", False):
                    sensor_names.append(name)
            return sensor_names
        log.error(f"Empty response from get_sensor_components_names(d_id={d_id})")
        raise EmptyCommandResponse("get_sensor_components_names")

    def get_algorithm_components_names(self, d_id: int, only_active: bool):
        """
        Get the names of algorithm components on the device.
        :param d_id: Device ID to get the algorithm component names for.
        :param only_active: If True, return only active algorithm components; otherwise, return all.
        :return: List of algorithm component names.
        """
        algo_names = []
        res = self.get_algorithm_components_status(d_id, only_active)
        if res is not None:
            for name, status in res.items():
                if not only_active or status.get("enable", False):
                    algo_names.append(name)
            return algo_names
        log.error(f"Empty response from get_algorithm_components_names(d_id={d_id})")
        raise EmptyCommandResponse("get_algorithm_components_names")

    def get_boolean_property(
        self, d_id: int, comp_name: str, prop_name: str, sub_prop_name=None
    ):
        """
        Get a boolean property value for a given component on the device.
        The property can be a simple property or a nested property.
        :param d_id: Device ID to get the property for.
        :param comp_name: Name of the component.
        :param prop_name: Name of the property.
        :param sub_prop_name: (Optional) Name of the sub-property if applicable.
        :return: Value of the boolean property.
        """
        res = self.hsd_dll.hs_datalog_get_boolean_property(
            d_id, comp_name, prop_name, sub_prop_name
        )
        if res[0]:
            return res[1]
        if sub_prop_name is None:
            log.error(
                f"Empty response from get_boolean_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name})"
            )
        else:
            log.error(
                f"Empty response from get_boolean_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name}, "
                f"sub_prop_name={sub_prop_name})"
            )
        raise EmptyCommandResponse("get_boolean_property")

    def get_integer_property(
        self, d_id: int, comp_name: str, prop_name: str, sub_prop_name=None
    ):
        """
        Get an integer property value for a given component on the device.
        The property can be a simple property or a nested property.
        :param d_id: Device ID to get the property for.
        :param comp_name: Name of the component.
        :param prop_name: Name of the property.
        :param sub_prop_name: (Optional) Name of the sub-property if applicable.
        :return: Value of the integer property.
        """
        res = self.hsd_dll.hs_datalog_get_integer_property(
            d_id, comp_name, prop_name, sub_prop_name
        )
        if res[0]:
            return res[1]
        if sub_prop_name is None:
            log.error(
                f"Empty response from get_integer_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name})"
            )
        else:
            log.error(
                f"Empty response from get_integer_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name}, "
                f"sub_prop_name={sub_prop_name})"
            )
        raise EmptyCommandResponse("get_integer_property")

    def get_float_property(
        self, d_id: int, comp_name: str, prop_name: str, sub_prop_name=None
    ):
        """
        Get a float property value for a given component on the device.
        The property can be a simple property or a nested property.
        :param d_id: Device ID to get the property for.
        :param comp_name: Name of the component.
        :param prop_name: Name of the property.
        :param sub_prop_name: (Optional) Name of the sub-property if applicable.
        :return: Value of the float property.
        """
        res = self.hsd_dll.hs_datalog_get_float_property(
            d_id, comp_name, prop_name, sub_prop_name
        )
        if res[0]:
            return res[1]
        if sub_prop_name is None:
            log.error(
                f"Empty response from get_float_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name})"
            )
        else:
            log.error(
                f"Empty response from get_float_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name}, "
                f"sub_prop_name={sub_prop_name})"
            )
        raise EmptyCommandResponse("get_float_property")

    def get_string_property(
        self, d_id: int, comp_name: str, prop_name: str, sub_prop_name=None
    ):
        """
        Get a string property value for a given component on the device.
        The property can be a simple property or a nested property.
        :param d_id: Device ID to get the property for.
        :param comp_name: Name of the component.
        :param prop_name: Name of the property.
        :param sub_prop_name: (Optional) Name of the sub-property if applicable.
        :return: Value of the string property.
        """
        res = self.hsd_dll.hs_datalog_get_string_property(
            d_id, comp_name, prop_name, sub_prop_name
        )
        if res[0]:
            return res[1]
        if sub_prop_name is None:
            log.error(
                f"Empty response from get_string_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name})"
            )
        else:
            log.error(
                f"Empty response from get_string_property(d_id={d_id}, "
                f"comp_name={comp_name}, prop_name={prop_name}, "
                f"sub_prop_name={sub_prop_name})"
            )
        raise EmptyCommandResponse("get_string_property")

    def set_property(
        self,
        d_id: int,
        value,
        comp_name: str,
        prop_name: str,
        sub_prop_name=None,
        sub_sub_prop_name=None,
    ):
        """
        Set a property value for a given component on the device.
        The property can be a simple property or a nested property.
        This method constructs the appropriate command message based on the provided
        parameters and sends it to the device.
        Note: If the value is an empty list, the method will return False without
        sending any command.

        :param d_id: Device ID to set the property for.
        :param value: Value to set for the property.
        :param comp_name: Name of the component.
        :param prop_name: Name of the property.
        :param sub_prop_name: (Optional) Name of the sub-property if applicable.
        :param sub_sub_prop_name: (Optional) Name of the sub-sub-property if applicable.
        :return: Result of the set property command.
        """
        if value == []:
            return False

        if sub_prop_name is None and sub_sub_prop_name is None:
            message = PnPLCMDManager.create_set_property_cmd(
                comp_name, prop_name, value
            )
        elif sub_prop_name is not None and sub_sub_prop_name is None:
            message = PnPLCMDManager.create_set_property_cmd(
                comp_name,
                prop_name,
                {sub_prop_name: value},
            )
        else:
            message = PnPLCMDManager.create_set_property_cmd(
                comp_name,
                prop_name,
                {sub_prop_name: {sub_sub_prop_name: value}},
            )
        res = self.hsd_dll.hs_datalog_send_message(d_id, message, len(message))

        if res:
            return res
        if sub_prop_name is None and sub_sub_prop_name is None:
            log.error(
                f"Empty response from set_property(d_id={d_id}, comp_name={comp_name}, "
                f"prop_name={prop_name})"
            )
        elif sub_prop_name is not None and sub_sub_prop_name is None:
            log.error(
                f"Empty response from set_property(d_id={d_id}, comp_name={comp_name}, "
                f"prop_name={prop_name}, sub_prop_name={sub_prop_name})"
            )
        else:
            log.error(
                f"Empty response from set_property(d_id={d_id}, comp_name={comp_name}, "
                f"prop_name={prop_name}, sub_prop_name={sub_prop_name}, "
                f"sub_sub_prop_name={sub_sub_prop_name})"
            )
        raise EmptyCommandResponse("set_property")

    def set_data_ready_callback(self):
        """
        Not implemented method.
        Set a callback function to be called when new data is available.
        :return: None
        """
        log.error("Not implemented function")
        raise EmptyCommandResponse("set_data_ready_callback")

    def __is_content_a_property(self, content):
        """
        Check if the given DTDL content is a property.
        :param content: DTDL content to check.
        :return: True if the content is a property, False otherwise.
        """
        if isinstance(content.type, list):
            return [c for c in content.type if c.value == "Property"] is not None
        else:
            return content.type.value == DTM.ContentType.PROPERTY.value

    def __set_component_values(self, d_id: int, comp, dtdl_comp):
        """
        Set component properties based on the provided component dictionary and
        DTDL component definition.
        :param d_id: Device ID to set properties for.
        :param comp: Dictionary containing component properties to set.
        :param dtdl_comp: DTDL component definition for property reference.
        :return: None
        """
        comp_name = list(comp.keys())[0]
        for content in dtdl_comp.contents:
            prop_check = self.__is_content_a_property(content)
            if prop_check and content.writable == True:
                if content.name in comp[comp_name].keys():
                    if isinstance(content.schema, str):
                        self.set_property(
                            d_id,
                            comp[comp_name][content.name],
                            comp_name,
                            content.name,
                        )
                    else:
                        #check property type (Enum, Object or others)
                        if content.schema.type == DTM.SchemaType.ENUM:
                            self.set_property(
                                d_id,
                                comp[comp_name][content.name],
                                comp_name,
                                content.name,
                            )
                        elif content.schema.type == DTM.SchemaType.OBJECT:
                            for field in content.schema.fields:
                                if isinstance(field.schema, DTM.ContentSchema):
                                    if field.schema.type == DTM.SchemaType.OBJECT:
                                        for sub_field in field.schema.fields:
                                            self.set_property(
                                                d_id,
                                                comp[comp_name][content.name][field.name][
                                                    sub_field.name
                                                ],
                                                comp_name,
                                                content.name,
                                                field.name,
                                                sub_field.name,
                                            )
                                    elif content.schema.type == DTM.SchemaType.ENUM:
                                        self.set_property(
                                            d_id,
                                            comp[comp_name][content.name],
                                            comp_name,
                                            content.name,
                                        )
                                else:
                                    self.set_property(
                                        d_id,
                                        comp[comp_name][content.name][field.name],
                                        comp_name,
                                        content.name,
                                        field.name,
                                    )
                else:
                    print(
                        f"[WARNING] - wrong property name in your Device Status --> "
                        f"Component: {comp_name}"
                    )

    def update_device(self, d_id: int, device_status_json_file_path, dtdl_components):
        """
        Update device properties based on a provided device status JSON file and DTDL components.
        Note: This method assumes that the device status JSON file is correctly formatted
        and that the DTDL components are provided as a dictionary for property reference.

        :param d_id: Device ID to update.
        :param device_status_json_file_path: Path to the device status JSON file.
        :param dtdl_components: Dictionary of DTDL components for property reference.
        :return: None
        """
        with open(device_status_json_file_path, encoding="utf-8") as f:
            device_dict = json.load(f)
            f.close()
        pres_res = self.get_device_presentation_string(d_id)
        if pres_res is not None:
            board_board_id = hex(pres_res["board_id"])
            board_fw_id = hex(pres_res["fw_id"])
            file_board_id = hex(device_dict["devices"][0]["board_id"])
            file_fw_id = hex(device_dict["devices"][0]["fw_id"])

            if board_board_id == file_board_id and board_fw_id == file_fw_id:
                for component in device_dict["devices"][d_id]["components"]:
                    for key in component.keys():
                        if key in dtdl_components:
                            self.__set_component_values(d_id, component, dtdl_components[key])
            else:
                log.error(
                    (
                        f"Wrong device_config.json selected. - ID from board: {pres_res}, "
                        f"- ID from file: {{'board_id':{file_board_id},'fw_id':{file_board_id}}}"
                    )
                )
                raise WrongDeviceConfigFile(
                    (
                        f"ID from board: {pres_res}, - ID from file: "
                        f"{{'board_id':{file_board_id},'fw_id':{file_board_id}}}"
                    )
                )

class PnPLHSD_Creator:
    """
    Creator class for PnPLHSD Command Manager.
    This class is responsible for creating instances of the PnPLHSD_CommandManager,
    which manages communication with PnPLHSD devices.
    
    Methods:
    - __create_cmd_set(): Create and return a command set instance for PnPLH
    - create_cmd_manager(...): Create and return a PnPLHSD_CommandManager instance.
    """
    def __create_cmd_set(self):
        """
        Create and return a command set instance for PnPLHSD.
        :return: An instance of the command set for PnPLHSD.
        """
        return "pnpl"
    def create_cmd_manager(self, plug_callback = None, unplug_callback = None):
        """
        Create and return a PnPLHSD_CommandManager instance. This method initializes
        the command set and
        passes optional callbacks for device plug and unplug events.
        :param plug_callback: Optional callback function for device plug events.
        :param unplug_callback: Optional callback function for device unplug events.
        :return: An instance of PnPLHSD_CommandManager.
        """
        cmd_set = self.__create_cmd_set()
        return PnPLHSD_CommandManager(cmd_set, plug_callback, unplug_callback)

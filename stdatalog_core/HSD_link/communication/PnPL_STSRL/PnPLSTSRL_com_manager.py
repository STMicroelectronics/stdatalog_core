
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
"""
PnPL over SSTL Command Manager.

This module provides the `PnPLSTSRL_CommandManager`, a helper to communicate with devices
using the PnPL command set over the SSTL transport protocol via a serial port.

Overview:
- Opens a serial connection and initializes the SSTL manager.
- Sends PnPL commands and waits for responses using a semaphore.
- Parses JSON responses returned by the device.
- Handles segmented SSTL packets (acknowledging as needed).
- Offers convenience methods to query device info, status, components, and set properties.

Key Classes:
- `PnPLSTSRL_CommandManager`: Main interface for sending/receiving PnPL commands via SSTL.
- `PnPLSTSRL_Creator`: Factory to create a command manager with the default command set.

Workflow:
1. Call `open(com_id, speed, timeout)` to connect and initialize SSTL.
2. Use methods like `get_device_status`, `get_component_status`, or `set_property`.
3. For logging, use `start_log` and `stop_log`.
4. When done, call `close()` to release resources.

Error Handling:
- Logs and raises `EmptyCommandResponse` if device responses are missing.
- Raises `WrongDeviceConfigFile` when the configuration file does not match the connected device.

Deprecations:
- `set_device_status(d_id, device_status_json_file_path)` is deprecated. Use
    `update_device(d_id, device_status_json_file_path, dtdl_components)`.

Usage Example:
        >>> from stdatalog_core.HSD_link.communication.PnPL_STSRL.PnPLSTSRL_com_manager import (
        ...     PnPLSTSRL_CommandManager,
        ... )
        >>> cm = PnPLSTSRL_CommandManager()
        >>> cm.open("COM3", speed=1843200)
        True
        >>> status = cm.get_device_status(0)
        >>> cm.close()

See Also:
- `stdatalog_core.HSD_link.communication.PnPL_STSRL.SSTL`
- `stdatalog_core.HSD_link.communication.PnPL_STSRL.ASPEP`
"""

import json
from threading import Semaphore
import warnings
import serial
import serial.tools.list_ports as list_ports

from stdatalog_pnpl.DTDL import device_template_model as DTM
from stdatalog_pnpl.PnPLCmd import PnPLCMDManager
from stdatalog_core.HSD_link.communication.PnPL_STSRL.SSTL import SSTL, SSTLProtocolError, SSTLCommandRequestType
from stdatalog_core.HSD_utils import logger
from stdatalog_core.HSD_utils.exceptions import EmptyCommandResponse, WrongDeviceConfigFile

log = logger.get_logger(__name__)

class PnPLSTSRLComManagerError(Exception):
    """High-level error raised by PnPLSTSRL_com_manager when getting serial data."""
    pass

class PnPLSTSRL_CommandManager:
    """
    PnPL over SSTL Command Manager.
    This class manages the communication with devices using the PnPL command set
    over the SSTL protocol via a serial port.
    It provides methods to open/close the serial connection, send commands,
    and receive responses from the device.
    Methods:
        - open(com_id, speed=1843200, timeout=None): Open the serial port connection.
        - close(): Close the serial port connection.
        - send_command(d_id, message): Send a command to the device and receive the response.
        - get_version(): Get the version of the PnPL command set.
        - get_device_info(d_id): Get the device information.
        - get_cmd_set_presentation_string(): Get the command set presentation string.
        - get_device_presentation_string(d_id): Get the device presentation string.
        - get_device_identity(d_id): Get the device identity.
        - get_device_alias(d_id): Get the device alias.
        - start_log(d_id, interface=3): Start logging on the device.
        - stop_log(d_id): Stop logging on the device.
        - get_devices(): Get the list of connected devices.
        - get_device_status(d_id): Get the device status.
        - get_device(d_id): Get the device status.
                - set_device_status(d_id, device_status_json_file_path):
                    Deprecated method to set device status.
                - get_sensor_components_status(d_id, type_filter, only_active):
                    Get sensor components status.
        - get_algorithm_components_status(d_id, only_active): Get algorithm components status.
        - get_actuator_components_status(d_id, only_active): Get actuator components status.
        - get_component_status(d_id, component_name): Get specific component status.
        - get_components_count(d_id): Get count of all components.
        - get_sensor_components_count(d_id, only_active): Get count of sensor components.
        - get_algorithm_components_count(d_id, only_active): Get count of algorithm components.
        - get_actuator_components_count(d_id, only_active): Get count of actuator components.
        - get_components_names(d_id): Get names of all components.
                - update_device(d_id, device_status_json_file_path, dtdl_components):
                    Update device configuration.
    Notes:
        - This class assumes that the serial port and SSTL manager are properly initialized.
        - Error handling is performed for command responses and serial communication.
        - A semaphore is used to synchronize sending and receiving messages.
        - The command set used is "pnpl".
    """

    def __init__(self, cmd_set = "pnpl"):
        self.cmd_set = cmd_set

        self.serial_port = None
        # When True, suppress noisy transport errors during shutdown/teardown
        self.suppress_errors = False

        # Create a semaphore
        self.serial_semaphore = Semaphore(0)

        self.sstl_manager = None
        self.sstl_packet = None
        self.sstl_packet_copy = None

    def set_suppress_errors(self, value: bool):
        """Enable/disable suppression of transport error logs.

        When enabled, low-level ASPEP/SSTL errors that typically occur during
        shutdown (e.g., when the port is closing) will not be logged and will
        be treated as no-data (returning None) by `get_serial_data()`.
        """
        self.suppress_errors = bool(value)

    def send_pnpl_msg(self, PnPL_msg):
        """
        Send a PnPL message and wait for the response.
        The method sends the provided PnPL message to the device via the serial port
        and waits for a response. It uses a semaphore to synchronize the sending
        and receiving of the message.
        Note:
            - This method assumes that the serial port and SSTL manager are properly initialized.
            - If no response is received within the timeout period, an error is logged.
            - The response is expected to be in JSON format.
        Parameters:
            PnPL_msg: str PnPL message to send.
        Returns:
            dict: Response from the device.
        """
        if self.sstl_manager is None:
            log.error("SSTL Manager is not initialized.")
            return None
        self.sstl_manager.send_command(self.serial_port, PnPL_msg, self.serial_semaphore)
        ok = self.serial_semaphore.acquire(timeout=2)
        if not ok:
            log.error("Timeout waiting for response to command: {}".format(PnPL_msg))
            return None
        if self.sstl_packet_copy is None:
            log.error("No packet received for the command: {}".format(PnPL_msg))
            return None
        try:
            print("SEND PnPL Msg UNLOCKED, Response:", getattr(self.sstl_packet_copy, 'data', b''))
            data = getattr(self.sstl_packet_copy, 'data', b'') or b''
            if data != b'':
                res = json.loads(data.decode())
                # reset buffer for next message
                self.sstl_packet_copy.data = b''
                return res
            else:
                log.error("Empty response payload for the command: {}".format(PnPL_msg))
                return None
        finally:
            # ensure we don't hold onto stale packet objects
            if self.sstl_manager and getattr(self.sstl_manager, 'tx_segmentation', False) is False:
                # in non-segmented mode, clear packet copy after processing
                self.sstl_packet_copy = None

    def receive_bytes(self):
        """
        Receive bytes from the serial port. This method handles different command
        responses based on the 3-bit control register (cr) value in the SSTL packet header.
        Note: This method assumes that the serial port and SSTL manager are properly initialized.
        Returns:
            sstl_packet: Received SSTL packet.
        Raises:
            SSTLProtocolError: If an error occurs while receiving the SSTL packet.
        """
        try:
            self.sstl_packet = self.sstl_manager.receive(self.serial_port)
            if self.sstl_packet is not None:
                if self.sstl_packet.header:
                    cr = self.sstl_packet.header.cr
                    match cr:
                        case SSTLCommandRequestType.BINARY_HSD_PAYLOAD.value:
                            return self.sstl_packet
                        case SSTLCommandRequestType.COMMAND_REQUEST.value:
                            return "ERROR"
                        case SSTLCommandRequestType.RESPONSE.value:
                            # Appending new data to the existing ones.
                            if self.sstl_packet_copy is None:
                                self.sstl_packet_copy = self.sstl_packet
                            else:
                                self.sstl_packet_copy.data += self.sstl_packet.data
                            if self.sstl_manager.tx_segmentation == False:
                                self.serial_semaphore.release()
                                return self.sstl_packet
                            else:
                                self.sstl_manager.send_ack(self.serial_port, self.sstl_packet_copy.header.sequence_num)
                                return self.sstl_packet_copy
                        case SSTLCommandRequestType.ASYNC_NOTIFICATION.value:
                            return self.sstl_packet
                        case SSTLCommandRequestType.BINARY_CUSTOM_PAYLOAD.value:
                            return self.sstl_packet
                        case SSTLCommandRequestType.TEXTUAL_DATA.value:
                            return self.sstl_packet
                return self.sstl_packet
        except SSTLProtocolError as e:
            self.serial_semaphore.release()
            if not self.suppress_errors:
                log.error("Error receiving SSTL packet: {}".format(e))
            # Propagate to get_serial_data where it may be downgraded
            raise e

    def get_serial_data(self):
        """
        Get data from the serial port.
        Returns:
            sstl_packet: Received SSTL packet.
        """
        try:
            serial_data = self.receive_bytes()
        except SSTLProtocolError as e:
            if self.suppress_errors or (self.serial_port is None or not getattr(self.serial_port, 'is_open', False)):
                # During shutdown or when port is closed, quietly indicate no data
                return None
            log.error("Error getting serial data: {}".format(e))
            raise PnPLSTSRLComManagerError("Error getting serial data") from e
        return serial_data

    def flush(self):
        """
        Flush the serial port input buffer.
        """
        if self.serial_port and self.serial_port.is_open:
            try:
                # self.serial_port.flush()
                self.serial_port.read_all()
            except Exception as e:
                if not self.suppress_errors:
                    log.error(f"Error flushing serial port: {e}")
                # If suppressing errors, ignore flushing failures (likely during teardown)
        else:
            if not self.suppress_errors:
                log.warning("Attempted to flush a closed or invalid serial port.")

    def open(self, com_id, speed=1843200, timeout=None):
        """
        Open the serial port connection.
        Parameters:
            com_id: str COM port identifier (e.g., 'COM3' or '/dev/ttyUSB0').
            speed: int Baud rate for the serial connection (default: 1843200).
            timeout: Optional[float] Timeout for serial read operations in seconds (default: None).
        Returns:
            bool: True if the port was opened successfully, False otherwise.
        """
        self.serial_port = serial.Serial(com_id, speed, timeout=timeout)
        self.sstl_manager = SSTL(self.serial_port)
        # Try to set a buffer size of 100 MB. 
        # Taking in account that device driver can ignore this value.
        if hasattr(self.serial_port, 'set_buffer_size'):
            self.serial_port.set_buffer_size(rx_size=104857600)
        return self.serial_port.is_open

    def close(self):
        """
        Close the serial port connection and reset related attributes.
        """
        if self.serial_port:
            # Close the serial connection
            self.serial_port.close()
            # Reset all serial connection attributes
            self.serial_port = None
            self.sstl_packet = None
            self.sstl_packet_copy = None
            self.serial_semaphore = Semaphore(0)
            self.sstl_manager = None
        else:
            log.error("Serial port not opened. Cannot close it.")

    def get_nof_devices(self):
        """
        Get the number of connected devices (serial ports).
        Note: This implementation counts all available serial ports without filtering.
        Improvements can be made to filter for specific device types if needed.
        -- To be done in future releases --
        Returns:
            int: Number of connected devices.
        """
        # Get a list of available serial ports
        ports = list_ports.comports()
        # Return the number of connected devices
        return len(ports)

    def send_command(self, d_id: int, message):
        """
        Send a command to the device.
        Parameters:
            d_id: int Device identifier (not used in this implementation).
                - within the implementation, the serial_port is used directly to send the command.
            message: str Command message to send.
        Returns:
            dict: Response from the device.
        """
        _ = d_id  # Unused parameter
        return self.send_pnpl_msg(message)

    def get_version(self):
        """
        Get the version of the PnPL command set.
        Returns:
            str: Version string.
        """
        return "0"

    def get_device_info(self, d_id:int):
        """
        Get the device information.
        Parameters:
            d_id: int Device identifier.
        Returns:
            dict: Device information.
        """
        return self.get_component_status(d_id, "firmware_info")

    def get_cmd_set_presentation_string(self):
        """
        Get the command set presentation string.
        Returns:
            str: Command set presentation string.
        """
        return "PnPL Commands"

    def get_device_presentation_string(self, d_id: int=None):
        """
        Get the device presentation string.
        Parameters:
            d_id: int Device identifier.
        Returns:
            dict: Device presentation string.
        """
        if d_id is not None:
            res = self.send_pnpl_msg(PnPLCMDManager.create_get_presentation_string_cmd())
            if res is not None:
                return res
            log.error(f"Empty response from get_device_identity(d_id={d_id})")
            raise EmptyCommandResponse("get_device_identity")

    def get_device_identity(self, d_id: int):
        """
        Get the device identity.
        Parameters:
            d_id: int Device identifier.
        Returns:
            dict: Device identity.
        """
        if d_id is not None:
            res = self.send_pnpl_msg(PnPLCMDManager.create_get_presentation_string_cmd())
            if res is not None:
                return res
            log.error(f"Empty response from get_device_identity(d_id={d_id})")
            raise EmptyCommandResponse("get_device_identity")

    def get_device_alias(self, d_id: int):
        """
        Get the device alias.
        Parameters:
            d_id: int Device identifier.
        Returns:
            str: Device alias.
        """
        return self.get_device_info(d_id)["firmware_info"]["alias"]

    def start_log(self, d_id: int, interface: int = 3):
        """
        Start the logging process on the device.
        Parameters:
            d_id: int Device identifier (not used in this implementation).
                - within the implementation, the serial_port is used directly to send the command.
            interface: int Interface identifier to use for logging (default for serial is 3).
        Returns:
            bool: True if the command was sent successfully.
        """
        _ = d_id  # Unused parameter
        self.send_pnpl_msg(
            PnPLCMDManager.create_command_cmd(
                "log_controller", "start_log", "interface", interface
            )
        )
        return True

    def stop_log(self, d_id: int):
        """
        Stop the logging process on the device.
        Parameters:
            d_id: int Device identifier (not used in this implementation).
                - within the implementation, the serial_port is used directly to send the command.
        Returns:
            bool: True if the command was sent successfully.
        """
        _ = d_id  # Unused parameter
        self.send_pnpl_msg(PnPLCMDManager.create_command_cmd("log_controller", "stop_log"))
        return True

    def get_devices(self):
        """
        Get the list of connected devices. This function scans available serial ports.
        This method redefine the way to get connected devices in the context of PnPL over STSRL.
        (serial communication vs USB enumeration in other contexts).
        Note: This implementation does not filter for specific device types.
        Returns:
            list: List of connected devices.
        """
        ports = list_ports.comports()
        return ports

    def get_device_status(self, d_id: int):
        """
        Get the device status.
        Parameters:
            d_id: int Device identifier.
        Returns:
            dict: Device status.
        """
        res = self.send_pnpl_msg(PnPLCMDManager.create_get_device_status_cmd())
        if res is not None:
            return res
        log.error(f"No Device Status[d_id:{d_id}] returned.")
        raise EmptyCommandResponse("get_device_status")

    def get_device(self, d_id: int):
        """
        Get the device status.
        Parameters:
            d_id: int Device identifier.
        Returns:
            dict: Device status.
        """
        return self.get_device_status(d_id)

    def set_device_status(self, d_id: int, device_status_json_file_path):
        """
        Deprecated: use ``update_device(d_id, device_status_json_file_path, dtdl_components)``.

        This method is kept as a no-op for backward compatibility and will be removed in a
        future release. It previously did nothing and returned ``None``. To actually update a
        device configuration, call ``update_device`` and provide the DTDL components map.

        Parameters:
            d_id: int
                Device identifier. Unused in this deprecated method.
            device_status_json_file_path: str
                Path to the JSON file containing the device status. Unused here.
        Returns:
            None
        """
        warnings.warn(
            (
                "set_device_status(d_id, device_status_json_file_path) is deprecated and will be "
                "removed in a future release. Use "
                "update_device(d_id, device_status_json_file_path, dtdl_components) instead."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        _ = d_id
        _ = device_status_json_file_path
        return None

    def __get_components_status(self, d_id: int, c_type:int=None):
        """
        Get the status of components filtered by type.
        Parameters:
            d_id: int Device identifier.
            c_type: Optional[int] Component type filter
                (0: sensor, 1: algorithm, 2: actuator).
                If None, no filtering is applied.
        Returns:
            dict: Status of components.
        """
        res = dict()
        devices = self.get_device_status(d_id)
        device_status = devices["devices"][0]
        for comp in device_status["components"]:
            comp_name = list(comp.keys())[0]
            comp_status = comp[comp_name]
            if c_type is not None:
                if "c_type" in comp_status and comp_status["c_type"] == c_type:
                    res[comp_name] = comp_status
            else:
                res[comp_name] = comp_status
        return res

    def get_sensor_components_status(self, d_id: int, type_filter, only_active):
        """
        Get the status of sensor components.
        Parameters:
            d_id: int Device identifier.
            type_filter: str Sensor type filter (e.g., "acc", "gyro"). If empty,
                no filtering is applied.
            only_active: bool If True, return only active sensors.
        Returns:
            dict: Status of sensor components.
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
                    if not status.get("enable", False) or sensor_type != type_filter.lower():
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
        Get the status of algorithm components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, return only active algorithms.
        Returns:
            dict: Status of algorithm components.
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

    def get_actuator_components_status(self, d_id: int, only_active: bool):
        """
        Get the status of actuator components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, return only active actuators.
        Returns:
            dict: Status of actuator components.
        """
        actuator_c = self.__get_components_status(d_id,3)
        if only_active == True:
            disabled_actuator_names = []
            for name, status in actuator_c.items():
                if not status.get("enable", False):
                    disabled_actuator_names.append(name)
            if len(disabled_actuator_names) > 0:
                for dsn in disabled_actuator_names:
                    del actuator_c[dsn]
        return actuator_c

    def get_component_status(self, d_id:int, component_name:str):
        """
        Get the status of a specific component.
        Parameters:
            d_id: int Device identifier (Not used in this implementation, but kept for consistency).
                - within the implementation, the serial_port is used directly to send the command.
            component_name: str Name of the component.
        Returns:
            dict: Status of the specified component.
        """
        _ = d_id  # Unused parameter
        return self.send_pnpl_msg(PnPLCMDManager.create_get_component_status_cmd(component_name))

    def get_components_count(self, d_id: int):
        """
        Get the count of all components.
        Parameters:
            d_id: int Device identifier.
        Returns:
            int: Count of all components.
        """
        return len(self.__get_components_status(d_id))

    def get_sensor_components_count(self, d_id: int, only_active: bool):
        """
        Get the count of sensor components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, count only active sensors.
        Returns:
            int: Count of sensor components.
        """
        return len(self.get_sensor_components_status(d_id, "", only_active))

    def get_algorithm_components_count(self, d_id: int, only_active: bool):
        """
        Get the count of algorithm components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, count only active algorithms.
        Returns:
            int: Count of algorithm components.
        """
        return len(self.get_algorithm_components_status(d_id, only_active))

    def get_actuator_components_count(self, d_id: int, only_active: bool):
        """
        Get the count of actuator components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, count only active actuators.
        Returns:
            int: Count of actuator components.
        """
        return len(self.get_actuator_components_status(d_id, only_active))

    def get_components_names(self, d_id: int):
        """
        Get the names of all components.
        Parameters:
            d_id: int Device identifier.
        Returns:
            list[str]: List of component names.
        """
        comp_names = []
        res = self.__get_components_status(d_id)
        if res is not None:
            for r in res:
                comp_names.append(r)
            return comp_names
        log.error(f"Empty response from get_components_names. d_id: {d_id}")
        raise EmptyCommandResponse("get_components_names")

    def get_sensor_components_names(self, d_id: int, type_filter: str, only_active: bool):
        """
        Get the names of sensor components.
        Parameters:
            d_id: int Device identifier.
            type_filter: str Sensor type filter (e.g., "acc", "gyro"). If empty,
                no filtering is applied.
            only_active: bool If True, return only active sensors.
        Returns:
            list[str]: List of sensor component names.
        """
        sensor_names = []
        res = self.get_sensor_components_status(d_id, type_filter, only_active)
        if res is not None:
            for name, status in res.items():
                if not only_active or status.get("enable", False):
                    sensor_names.append(name)
            return sensor_names
        log.error(f"Empty response from get_sensor_components_names. d_id: {d_id}")
        raise EmptyCommandResponse("get_sensor_components_names")

    def get_algorithm_components_names(self, d_id: int, only_active: bool):
        """
        Get the names of algorithm components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, return only active algorithms.
        Returns:
            list[str]: List of algorithm component names.
        """
        algo_names = []
        res = self.get_algorithm_components_status(d_id, only_active)
        if res is not None:
            for name, status in res.items():
                if not only_active or status.get("enable", False):
                    algo_names.append(name)
            return algo_names
        log.error(f"Empty response from get_algorithm_components_names. d_id: {d_id}")
        raise EmptyCommandResponse("get_algorithm_components_names")

    def get_actuator_components_names(self, d_id: int, only_active: bool):
        """
        Get the names of actuator components.
        Parameters:
            d_id: int Device identifier.
            only_active: bool If True, return only active actuators.
        Returns:
            list[str]: List of actuator component names.
        """
        actuator_names = []
        res = self.get_actuator_components_status(d_id, only_active)
        if res is not None:
            for name, status in res.items():
                if not only_active or status.get("enable", False):
                    actuator_names.append(name)
            return actuator_names
        log.error(f"Empty response from get_actuator_components_names. d_id: {d_id}")
        raise EmptyCommandResponse("get_actuator_components_names")

    def __get_property_value(
        self,
        d_id: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Get a property value for a given component.
        Parameters:
            d_id: int Device identifier.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            The value of the specified property.
        """
        c_status = self.get_component_status(d_id, comp_name)
        if sub_prop_name is None:
            return c_status.get(comp_name).get(prop_name)
        else:
            return c_status.get(comp_name).get(prop_name).get(sub_prop_name)

    def get_boolean_property(
        self,
        d_id: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Get a boolean property value for a given component.
        Parameters:
            d_id: int Device identifier.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            bool: The value of the specified boolean property.
        """
        return self.__get_property_value(d_id, comp_name, prop_name, sub_prop_name)

    def get_integer_property(
        self,
        d_id: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Get an integer property value for a given component.
        Parameters:
            d_id: int Device identifier.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            int: The value of the specified integer property.
        """
        return self.__get_property_value(d_id, comp_name, prop_name, sub_prop_name)

    def get_float_property(
        self,
        d_id: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Get a float property value for a given component.
        Parameters:
            d_id: int Device identifier.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            float: The value of the specified float property.
        """
        return self.__get_property_value(d_id, comp_name, prop_name, sub_prop_name)

    def get_string_property(
        self,
        d_id: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Get a string property value for a given component.
        Parameters:
            d_id: int Device identifier.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            str: The value of the specified string property.
        """
        return self.__get_property_value(d_id, comp_name, prop_name, sub_prop_name)

    def set_property(
        self,
        d_id: int,
        value,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str = None,
    ):
        """
        Set a property value for a given component.
        Parameters:
            d_id: int Device identifier (Not used in this implementation, but kept for consistency).
                - within the implementation, the serial_port is used directly to send the command.
            value: bool, int, float, str Value to set for the property.
            comp_name: str Component name.
            prop_name: str Property name.
            sub_prop_name: Optional[str] Sub-property name (if applicable).
        Returns:
            dict: Response from the command manager after setting the property.
        """
        _ = d_id  # Unused parameter
        res = None
        if value == []:
            return False
        if isinstance(value, (bool, int, float, str)):
            if sub_prop_name is None:
                res = self.send_pnpl_msg(
                    PnPLCMDManager.create_set_property_cmd(comp_name, prop_name, value)
                )
            else:
                res = self.send_pnpl_msg(
                    PnPLCMDManager.create_set_property_cmd(
                        comp_name,
                        prop_name,
                        {sub_prop_name: value},
                    )
                )
        return res

    def __is_content_a_property(self, content):
        """
        Check if a DTDL content is a property.
        Parameters:
            content: DTM.Content DTDL content to check.
            Returns: bool indicating if the content is a property.
        """
        if isinstance(content.type, list):
            return [c for c in content.type if c.value == "Property"] is not None
        else:
            return content.type.value == DTM.ContentType.PROPERTY.value

    def __set_component_values(self, d_id: int, comp, dtdl_comp):
        """
        Set the values of a component based on the provided DTDL component definition.
        This method iterates through the contents of the DTDL component,
        identifies writable properties, and sets their values using the provided
        component dictionary.
        Note: The component dictionary is expected to have the same structure as the DTDL component.
        Parameters:
            d_id: int Device identifier.
            comp: dict Component dictionary containing property values to set.
            dtdl_comp: DTM.Component DTDL component definition.
        Returns:
            None
        Raises:
            Warning: If a property name in the component dictionary does not match any property
                in the DTDL component definition.
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
        Update the device status based on a provided JSON file and DTDL components.
        This method reads the device status from a JSON file, verifies the device identity,
        and updates the component values according to the DTDL definitions.
        Note: The device identity is checked by comparing board_id and fw_id from the device
        status with those in the JSON file.
        Parameters:
            d_id: int
                Device identifier.
            device_status_json_file_path: str
                Path to the JSON file containing the device status.
            dtdl_components: dict
                Dictionary mapping component names to their DTDL definitions.
        Raises:
            WrongDeviceConfigFile:
                If the device configuration file does not match the connected device.
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
                        f"Wrong device_config.json selected. - ID from board: "
                        f"b_id{board_board_id}, fw_id{board_fw_id} - ID from file: "
                        f"b_id{file_board_id}, fw_id{file_fw_id}"
                    )
                )
                raise WrongDeviceConfigFile(
                    (
                        f"ID from board: b_id{board_board_id}, fw_id{board_fw_id} - "
                        f"ID from file: b_id{file_board_id}, fw_id{file_fw_id}"
                    )
                )

class PnPLSTSRL_Creator:
    """
    Create and return a PnPLSTSRL_CommandManager instance.
    Methods:
        create_cmd_manager():
            Creates and returns a PnPLSTSRL_CommandManager instance.
    Returns:
        PnPLSTSRL_CommandManager: The command manager instance.
    """
    def __create_cmd_set(self):
        return "pnpl"

    def create_cmd_manager(self):
        """
        Create and return a PnPLSTSRL_CommandManager instance.
        This method initializes the command set and passes it to the command manager.
        Returns:
            PnPLSTSRL_CommandManager: The command manager instance.
        """
        cmd_set = self.__create_cmd_set()
        return PnPLSTSRL_CommandManager(cmd_set)

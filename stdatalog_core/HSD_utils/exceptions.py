# *****************************************************************************
#  * @file    exceptions.py
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
HSDatalog exceptions module.
This module centralizes the exception hierarchy used across the HSDatalog Python SDK
and its PnPL (Plug and Play) interface, providing clear, contextual, and structured
error signaling for model validation, device interaction, data extraction, and
native library operations.
Exception groups:
- Core HSD errors (base: HSDError)
    - MissingDeviceModelError: The device model (board/firmware pair) is not present in the catalog.
    - MissingSensorModelError: A sensor model is not defined within the device model.
    - MissingComponentModelError: A component model is not defined within the sensor model.
    - MissingAcquisitionInfoError: Acquisition information is not available in the device model.
    - MissingPropertyError: A required property is not present in the model.
    - UnsupportedSensorCategoryError: A sensor category is not supported by the SDK.
- Sensor parameter errors (base: SensorParamsError)
    - SensorIDError: Invalid or out-of-range sensor ID.
    - SubSensorIDError: Invalid or out-of-range sub-sensor ID.
    - SubSensorTypeError: Unexpected sub-sensor type.
    - NSensorAxesError: Inconsistent or unsupported number of sensor axes.
- File and data errors
    - MissingFileForSensorError: A required file for a specific sensor is missing.
    - MissingTagsException: No tags are present in an acquisition.
    - MissingISPUOutputDescriptorException: Missing ISPU output description file for a given sensor.
    - DataExtractionError: A failure occurred while extracting data (optionally typed) for a sensor.
    - DataCorruptedException: Corruption detected in a .dat file (e.g., missing packets).
    - NoDataAtIndexError: No data found at a specific index in a file.
- Feature and conversion errors
    - FeaturExtractorError: Failure during feature extraction for a sensor.
        Note: The class name is intentionally spelled "FeaturExtractorError".
    - NanoEdgeConversionError: Failure during NanoEdge.ai conversion for a sensor.
- Platform and device errors
    - NoDeviceConnectedError: No device is detected/connected.
    - InvalidCommandSetError: An unsupported or invalid device command set was provided.
    - UnsupportedPlatformError: The running platform is not supported.
- HSD library (native bridge) errors (base: HSDLibError)
    - MemoryFreeError: Failure while freeing memory allocated by the native library.
    - CommunicationEngineOpenError: Failure while opening the communication engine.
    - CommunicationEngineCloseError: Failure while closing the communication engine.
    - DeviceDisconnectedError: Device unexpectedly disconnected.
    - AcquisitionFormatError: The acquisition format is invalid or unsupported.
    - EmptyCommandResponse: A command returned an empty response.
    - SETCommandError: A SET command failed at the native/library layer.
- PnPL errors (base: PnPLError)
    - PnPLCommandError: Failure while executing a PnPL command.
    - PnPLSETDeviceStatusCommandError: Failure while executing a PnPL SET Device Status command.
    - WrongDeviceConfigFile: The provided device configuration file is invalid or incompatible.
Design notes:
- Most exceptions carry contextual details (e.g., board_id, fw_id, sensor_name, file_path, indices)
    to simplify diagnostics and logging.
- Messages are concise and formatted to be human-readable while remaining machine-parsable.
- The hierarchy enables coarse- or fine-grained exception handling depending on the caller’s needs.
Usage examples:
- Raising with context:
        raise MissingDeviceModelError(board_id="STEVAL-XYZ", fw_id="1.2.3")
        raise MissingPropertyError("odr")
        raise DataExtractionError(sensor_name="accelerometer", sensor_type="ACC")
        raise NoDataAtIndexError(index=1024, file_path="data/log.dat", size=4096)
- Handling at different levels:
        try:
                extract_data(...)
        except DataExtractionError as exc:
                logger.error("Sensor data extraction failed: %s", exc)
        except HSDError as exc:
                logger.error("HSD error: %s", exc)
        except PnPLError as exc:
                logger.error("PnPL error: %s", exc)
"""

class HSDError(Exception):
    """Base exception class for HSDatalog errors."""

class MissingDeviceModelError(HSDError):
    """Exception for missing device model in the catalog."""
    def __init__(self, board_id = None, fw_id= None):
        """
        Initialize with board_id and fw_id for context.
        :param board_id: Identifier of the board.
        :param fw_id: Firmware identifier.
        """
        super().__init__(f"board_id:{board_id}, fw_id:{fw_id}")

class MissingSensorModelError(HSDError):
    """Exception for missing sensor model in the device model."""

class MissingComponentModelError(HSDError):
    """Exception for missing component model in the sensor model."""

class MissingAcquisitionInfoError(HSDError):
    """Exception for missing acquisition info in the device model."""

class MissingPropertyError(HSDError):
    """Exception for missing property in the model."""
    def __init__(self, prop_name):
        """
        Initialize with property name for context.
        :param prop_name: Name of the missing property.
        """
        super().__init__(f"{prop_name}")

class SensorParamsError(HSDError):
    """Exception for errors in sensor parameters."""
    def __init__(self, sensor_name):
        """
        Initialize with sensor name for context.
        :param sensor_name: Name of the sensor with parameter error.
        """
        super().__init__(f"{sensor_name}")
class SensorIDError(SensorParamsError):
    """Exception for errors related to sensor ID."""

class SubSensorIDError(SensorParamsError):
    """Exception for errors related to sub-sensor ID."""

class SubSensorTypeError(SensorParamsError):
    """Exception for errors related to sub-sensor type."""

class NSensorAxesError(SensorParamsError):
    """Exception for errors related to number of sensor axes."""

class UnsupportedSensorCategoryError(HSDError):
    """Exception for unsupported sensor category."""

class MissingFileForSensorError(HSDError):
    """Exception for missing required file for a sensor."""
    def __init__(self, file_path, sensor_name):
        """
        Initialize with file path and sensor name for context.
        :param file_path: Path of the missing file.
        :param sensor_name: Name of the sensor associated with the file.
        """
        super().__init__(f"{file_path} --x {sensor_name}")

class MissingTagsException(HSDError):
    """Exception for missing tags in acquisition."""
    def __init__(self, msg="No Tags found in your acquisition"):
        """
        Initialize with an optional message.
        :param msg: Custom message for the exception."""
        super().__init__(msg)

class MissingISPUOutputDescriptorException(HSDError):
    """Exception for missing ISPU output description file."""
    def __init__(self, ispu_sensor_name):
        """
        Initialize with ISPU sensor name for context.
        :param ispu_sensor_name: Name of the ISPU sensor.
        """
        super().__init__(f"Missing ISPU output description file for {ispu_sensor_name} sensor")

class DataExtractionError(HSDError):
    """Exception for errors during data extraction from files."""
    def __init__(self, sensor_name, sensor_type= None):
        """
        Initialize with sensor name and optional sensor type for context.
        :param sensor_name: Name of the sensor.
        :param sensor_type: Type of the sensor (optional).
        """
        if sensor_type is not None:
            super().__init__(f"{sensor_name}_{sensor_type}")
        else:
            super().__init__(f"{sensor_name}")

class DataCorruptedException(HSDError):
    """Exception for corrupted data due to missing packets."""
    def __init__(self, dat_file_path):
        """
        Initialize with the path of the corrupted data file.
        :param dat_file_path: Path to the .dat file with corrupted data.
        """
        super().__init__(f"Error extracting data from {dat_file_path} file. \
                        Missing packets revealed, data corrupted.")

class NoDataAtIndexError(HSDError):
    """Exception for absence of data at a specific index in a file."""
    def __init__(self, index, file_path, size):
        """
        Initialize with index, file path, and size for context.
        :param index: Index where data is missing.
        :param file_path: Path to the file.
        :param size: Size of the data expected.
        """
        super().__init__(f"index: {index}, file: {file_path}, size: {size}")

class FeaturExtractorError(HSDError):
    """Exception for errors during feature extraction."""
    def __init__(self, sensor_name):
        """
        Initialize with sensor name for context.
        :param sensor_name: Name of the sensor.
        """
        super().__init__(f"{sensor_name}")

class NanoEdgeConversionError(HSDError):
    """Exception for errors during NanoEdge conversion."""
    def __init__(self, sensor_name):
        """
        Initialize with sensor name for context.
        :param sensor_name: Name of the sensor.
        """
        super().__init__(f"{sensor_name}")

class NoDeviceConnectedError(HSDError):
    """Exception for no device connected scenario."""

class InvalidCommandSetError(HSDError):
    """Exception for invalid command set."""
    def __init__(self, dev_com_set):
        """
        Initialize with the invalid command set.
        :param dev_com_set: The invalid command set.
        """
        super().__init__(f"{dev_com_set}")

class UnsupportedPlatformError(HSDError):
    """Exception for unsupported platform."""
    def __init__(self, platform):
        """
        Initialize with the unsupported platform.
        :param platform: The unsupported platform.
        """
        super().__init__(f"{platform}")

class HSDLibError(HSDError):
    """Base exception class for HSDatalog library errors."""
    def __init__(self, command_name):
        """
        Initialize with command name for context.
        :param command_name: Name of the command that caused the error.
        """
        super().__init__(f"{command_name}")
class MemoryFreeError(HSDLibError):
    """Exception for memory free errors in HSDatalog library."""
    def __init__(self):
        """
        Initialize the MemoryFreeError with a specific message.
        """
        super().__init__("__hs_datalog_free")

class CommunicationEngineOpenError(HSDLibError):
    """Exception for communication engine open errors in HSDatalog library."""
    def __init__(self):
        """
        Initialize the CommunicationEngineOpenError with a specific message.
        """
        super().__init__("hs_datalog_open")
class CommunicationEngineCloseError(HSDLibError):
    """Exception for communication engine close errors in HSDatalog library."""
    def __init__(self):
        """
        Initialize the CommunicationEngineCloseError with a specific message.
        """
        super().__init__("hs_datalog_close")

class DeviceDisconnectedError(HSDLibError):
    """Exception for device disconnection errors in HSDatalog library."""
    def __init__(self):
        """
        Initialize the DeviceDisconnectedError with a specific message.
        """
        super().__init__("Device Disconnected")
class AcquisitionFormatError(HSDError):
    """Exception for acquisition format errors."""

class EmptyCommandResponse(HSDLibError):
    """Exception for empty command response errors in HSDatalog library."""

class SETCommandError(HSDLibError):
    """Exception for SET command errors in HSDatalog library."""

class PnPLError(Exception):
    """Base exception class for PnPL errors."""

class PnPLCommandError(PnPLError):
    """Exception for PnPL command errors."""
    def __init__(self, command_name):
        """
        Initialize with command name for context.
        :param command_name: Name of the command that caused the error.
        """
        super().__init__(f"{command_name}")

class PnPLSETDeviceStatusCommandError(PnPLCommandError):
    """Exception for PnPL SET Device Status command errors."""

class WrongDeviceConfigFile(PnPLError):
    """Exception for wrong device configuration file."""
    def __init__(self, error_message):
        """
        Initialize with error message for context.
        :param error_message: The error message describing the issue.
        """
        super().__init__(f"{error_message}")

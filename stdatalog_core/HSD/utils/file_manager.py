"""File naming and discovery utilities for STDatalog outputs.

This module provides helpers to encode/decode sensor data filenames and to
discover expected `.dat` files based on the device model or list actual files
present in an acquisition folder.
"""

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
# Module purpose:
#   - Provide helpers to encode/decode sensor data file names and to discover
#     expected data files from a device model and actual .dat files on disk.
#
# Design notes:
#   - Filename convention assumed:
#       * With sub-sensor: "<sensor_name>_<sub_sensor_type>.dat"
#       * Without sub-sensor: "<sensor_name>.dat"
#   - Device model object is expected to expose:
#       * device.sensor -> iterable of sensor models
#       * sensor.name -> str
#       * sensor.sensor_status.sub_sensor_status -> list with "is_active" bools
#       * sensor.sensor_descriptor.sub_sensor_descriptor -> list with "sensor_type" str
#
# Error handling:
#   - Functions assume well-formed inputs; minimal validation is performed.
#   - OS operations propagate exceptions (e.g., path not found).

import os


class FileManager:
    """
    Utilities for file naming and discovery related to STDatalog outputs.

    Responsibilities:
        - Encode/decode filenames following the STDatalog convention.
        - Compute the list of expected data files from a device model.
        - List .dat files in an acquisition folder.
        - Find a file by name within a directory tree.

    Notes:
        - All methods are static for convenience; no instance state is kept.
    """

    @staticmethod
    def decode_file_name(file_name):
        """
        Decode a data filename into sensor_name and sensor_type.

        Expected formats:
            - "<sensor_name>.dat" -> returns ("<sensor_name>", "<sensor_name>" last token)
            - "<sensor_name>_<sensor_type>.dat" -> returns ("<sensor_name>", "<sensor_type>")

        Parameters:
            file_name: str
                Filename including extension (e.g., 'accel_x.dat' or 'ism330dhcx_accel.dat').

        Returns:
            tuple[str, str]: (sensor_name, sensor_type)

        Caveats:
            - Splitting is based on '_' and '.' delimiters; unexpected formats may yield
                incorrect results. Ensure filenames follow the convention.
        """
        # Split "name.ext" then split name by underscores to separate last token as sub-type.
        sn = file_name.split(".")[0].split("_")
        # Join all but last token as sensor_name, last token as sensor_type.
        [sensor_name, sensor_type] = ["_".join(sn[:-1]), sn[-1]]
        return sensor_name, sensor_type

    @staticmethod
    def encode_file_name(sensor_name, sub_sensor_type=None, ext=".dat"):
        """
        Build a data filename following the STDatalog convention.

        Parameters:
            sensor_name: str
                Base sensor name (e.g., 'ism330dhcx_acc').
            sub_sensor_type: Optional[str]
                Sub-sensor type (e.g., 'accel', 'gyro'). If None, no suffix is added.
            ext: str
                File extension to use. Default '.dat'.

        Returns:
            str: Filename, e.g. 'ism330dhcx_acc_accel.dat' or 'ism330dhcx_acc.dat'.
        """
        if sub_sensor_type is None:
            file_name = sensor_name + ext
        else:
            file_name = sensor_name + "_" + sub_sensor_type + ext
        return file_name

    @staticmethod
    def get_file_names_from_model(device):
        """
        Compute the list of expected data filenames from a device model.

        The device model is expected to align sensor descriptors and statuses:
            - For each sensor, if sub_sensor_status[i].is_active is True,
                include a filename combining sensor.name and sub_sensor_descriptor[i].sensor_type.

        Parameters:
            device: Any
                Device configuration/model object exposing:
                - device.sensor (iterable)
                - sensor.name (str)
                - sensor.sensor_status.sub_sensor_status (list with is_active bool)
                - sensor.sensor_descriptor.sub_sensor_descriptor (list with sensor_type str)

        Returns:
            list[str]: Filenames expected in the datalog (e.g., for validation or checks).

        Raises:
            AttributeError/IndexError:
                If the model does not conform to the expected structure.
        """
        data_files_list = []
        for s in device.sensor:
            sss_list = s.sensor_status.sub_sensor_status
            for i, ssd in enumerate(s.sensor_descriptor.sub_sensor_descriptor):
                # Include only active sub-sensors
                if sss_list[i].is_active:
                    file_name = FileManager.encode_file_name(s.name, ssd.sensor_type)
                    data_files_list.append(file_name)
        return data_files_list

    @staticmethod
    def get_dat_files_from_folder(acq_folder_path):
        """
        List .dat files present in an acquisition folder (excluding internal check files).

        Filtering rules:
            - Include files ending with '.dat'.
            - Exclude files containing '_checked' (internal markers).

        Parameters:
            acq_folder_path: str
                Path to the acquisition folder.

        Returns:
            list[str]: Filenames that match the criteria.

        Raises:
            FileNotFoundError/PermissionError:
                If the folder is not accessible; exceptions propagate from os.scandir.
        """
        file_names = []
        with os.scandir(acq_folder_path) as list_of_entries:
            for entry in list_of_entries:
                # Consider only regular files, not directories.
                if entry.is_file():
                    # Exclude internal checked files; include only .dat payload files.
                    if entry.name.endswith(".dat") and "_checked" not in entry.name:
                        file_names.append(entry.name)
        return file_names

    @staticmethod
    def find_file(name: str, path: str):
        """
        Find a file by name within a directory tree.

        Parameters:
            name: str
                The exact filename to search (case-sensitive on non-Windows).
            path: str
                Root directory path to search recursively.

        Returns:
            str | None:
                Full path to the file if found, otherwise None.

        Notes:
            - Uses os.walk; performance depends on directory size.
            - Stops and returns on the first occurrence of the file.
        """
        # Walk through the directory tree starting at 'path'.
        for root, _, files in os.walk(path):
            # If the file is found, return the full path to the file.
            if name in files:
                return os.path.join(root, name)
        # If the file is not found, return None.
        return None

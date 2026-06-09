#
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
Dummy ADEV/HSD Command Manager.

This module provides a minimal, dummy implementation of a command manager for ADEV over HSD.
It is intended for testing, demos, or placeholder usage where a full communication stack is
not required.

Overview:
- Logs when the dummy engine is brought up or down.
- Returns static strings for identity and version.
- Reports zero devices found.

Key Classes:
- `ADEVHSD_Cmd`: Simple command-set descriptor with a `get_name()` helper.
- `ADEVHSD_CommandManager`: Dummy manager that logs engine lifecycle and exposes basic queries.
- `ADEVHSD_Creator`: Factory that builds the command manager with the default command set.

Usage Example:
    >>> from stdatalog_core.HSD_link.communication.ADEV_HSD.ADEVHSD_com_manager import (
    ...     ADEVHSD_Creator,
    ... )
    >>> cm = ADEVHSD_Creator().create_cmd_manager()
    >>> cm.get_version()
    '7.7.7'
    >>> cm.get_nof_devices()
    0
"""

import stdatalog_core.HSD_utils.logger as logger
log = logger.get_logger(__name__)

class ADEVHSD_Cmd:
    """
    Descriptor for the ADEV/HSD command set.
    Methods:
        - get_name(): Return the human-readable command set name.
    """

    def get_name(self):
        """
        Get the command set name.
        Returns:
            str: Name of the command set.
        """
        return "ADEVHSDCmd"

class ADEVHSD_CommandManager:
    """
    Dummy command manager for ADEV over HSD.
    This class simulates a minimal communication engine by logging lifecycle events and
    returning static information.
    Methods:
        - get_device_presentation_string(): Return a dummy device identifier string.
        - get_cmd_set_presentation_string(cmd_set): Present the command set in a readable form.
        - get_version(): Return a fixed version string.
        - get_nof_devices(): Return the number of detected devices (always 0 in this dummy).
    """

    def __init__(self, cmd_set: ADEVHSD_Cmd):
        """
        Initialize the dummy command manager.
        Parameters:
            cmd_set: ADEVHSD_Cmd
                Command set descriptor used to label this manager.
        Returns:
            None
        """
        _ = cmd_set  # Unused parameter kept for API consistency
        log.info("DUMMY Communication Engine UP")

    def __del__(self):
        """
        Destructor: log the engine shutdown.
        Returns:
            None
        """
        log.info("DUMMY Communication Engine DOWN")

    def get_device_presentation_string(self):
        """
        Get a dummy device presentation string.
        Returns:
            str: Fixed identifier string.
        """
        return "ADEV"

    def get_cmd_set_presentation_string(self, cmd_set):
        """
        Get the command set presentation string for this manager.
        Parameters:
            cmd_set: ADEVHSD_Cmd
                Command set descriptor.
        Returns:
            str: Human-readable presentation string.
        """
        return f"ADEVHSD_CommandManager command_set: {cmd_set.get_name()}"

    def get_version(self):
        """
        Get the dummy version string.
        Returns:
            str: Fixed version string.
        """
        return "7.7.7"

    def get_nof_devices(self):
        """
        Get the number of detected devices.
        Returns:
            int: Always 0 in the dummy implementation.
        """
        return 0

class ADEVHSD_Creator:
    """
    Factory class for the dummy ADEV/HSD command manager.
    Methods:
        - __create_cmd_set(): Create and return the default ADEV command-set descriptor.
        - create_cmd_manager(): Create and return a configured `ADEVHSD_CommandManager`.
    """

    def __create_cmd_set(self):
        """
        Create and return the default command-set descriptor.
        Returns:
            ADEVHSD_Cmd: Command-set descriptor instance.
        """
        return ADEVHSD_Cmd()

    def create_cmd_manager(self):
        """
        Create and return the dummy command manager.
        Returns:
            ADEVHSD_CommandManager: Configured dummy command manager instance.
        """
        return ADEVHSD_CommandManager(self.__create_cmd_set())

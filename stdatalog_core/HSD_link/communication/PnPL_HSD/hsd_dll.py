
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
"""ctypes-based wrapper for the `libhs_datalog_v2` shared library.

This module provides a platform-aware loader for the HSDatalog native library and exposes a
Pythonic API to interact with devices via PnPL commands and data transfer functions.

Design notes:
- Dynamically loads the proper binary for Windows, Linux (including Raspberry Pi variants),
    and macOS based on `platform.system()` and `platform.machine()`.
- Uses `ctypes` to configure function signatures, return types, and argument types.
- `HSD_Dll_Wrapper` binds raw C functions; `HSD_Dll` offers ergonomic helpers and memory
    management.

Error handling:
- Functions generally return `[success, value]`-style lists or booleans.
- Native pointers allocated by the DLL are freed via `hs_datalog_free` when present.
"""
# pylint: disable=invalid-name

import sys
import os
import ctypes
import platform
from ctypes import util, cdll

import stdatalog_core.HSD_utils.logger as logger

ST_HS_DATALOG_OK = 0
ST_HS_DATALOG_ERROR = -1
ptrs = []

def wrap_hsd_function(lib, funcname, restype, argtypes):
    """Wrap and configure a C function from a loaded shared library.

    Parameters:
    - lib: ctypes library handle (e.g., from `cdll.LoadLibrary`).
    - funcname: str symbol name exported by the shared library.
    - restype: ctypes type returned by the function.
    - argtypes: list|tuple of ctypes argument types.

    Returns:
    - callable: the function with `restype` and `argtypes` configured.
    """
    # Obtain the raw symbol from the loaded DLL. Using `__getattr__` avoids
    # attribute errors when the symbol is not present (will raise at access).
    func = lib.__getattr__(funcname)
    # Assign ctypes return type and argument types so calls perform proper
    # marshalling/unmarshalling and type checking.
    func.restype = restype
    func.argtypes = argtypes
    return func

class hsd_data_ready_callback(ctypes.Structure):
    """ctypes Structure defining a data-ready callback signature.

    Callback prototype:
    - `(int, int, int, POINTER(uint8), int) -> int`

    Notes:
    - The actual callback is provided by the user code and passed to the DLL.
    """
    # CFUNCTYPE defines a native function pointer type which the DLL can call
    # back into Python. The arguments are the data identifiers and a buffer.
    _fields_ = [
        ('callback', ctypes.CFUNCTYPE(
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_int,))
    ]

class HSD_Dll_Wrapper:
    """Low-level wrapper around `libhs_datalog_v2` functions.

    Responsibilities:
    - Load the correct shared library for the current platform/architecture.
    - Map exported C functions to Python callables with proper `ctypes` signatures.

    Attributes:
    - `_hsd_dll`: the loaded shared library handle.
    - Various `hs_datalog_*` callables configured with `restype` and `argtypes`.
    """

    def __init__(self):
        """Initialize the wrapper by loading the library and binding functions.

        Platform-specific behavior:
        - Linux: selects armv7l/aarch64/RPi variants or generic x86_64.
        - Windows: chooses 32/64-bit subfolder and updates `PATH` to locate the DLL.
        - macOS: selects `arm64` or `x86_64` dylib.

        Side effects:
        - Populates attributes with callable function references for subsequent use.
        """

        # Log platform architecture for troubleshooting binary selection.
        print(
            f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
            f"Platform Architecture: {platform.architecture()[0]}"
        )
        dll_name = ""
        # Platform-specific resolution: choose the right binary per OS/arch.
        if platform.system() == 'Linux':
            # Raspberry Pi v3 (armv7l) uses a dedicated build.
            if platform.machine() == 'armv7l':
                base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                dll_name = os.path.join(
                    base, "libhs_datalog", "raspberryPi", "libhs_datalog_v2.so"
                )
            # Raspberry Pi 4 (aarch64) may be 32-bit or 64-bit userland.
            elif platform.machine() == 'aarch64':
                arch = platform.architecture()[0]
                if arch == "32bit":
                    base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                    dll_name = os.path.join(
                        base,
                        "libhs_datalog",
                        "raspberryPi4_32bit",
                        "libhs_datalog_v2.so",
                    )
                else:
                    base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                    dll_name = os.path.join(
                        base,
                        "libhs_datalog",
                        "raspberryPi4_64bit",
                        "libhs_datalog_v2.so",
                    )
            # Generic Linux x86_64 build.
            else:
                base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                dll_name = os.path.join(
                    base, "libhs_datalog", "linux", "libhs_datalog_v2.so"
                )
            # Load ELF shared object.
            self._hsd_dll = cdll.LoadLibrary(dll_name)
        elif platform.system() == 'Windows':
            # Select 32/64-bit folder using Python's architecture descriptor.
            dll_subfolder = platform.architecture()[0]
            dll_name = "libhs_datalog_v2"
            base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
            dllabspath = os.path.join(base, "libhs_datalog", dll_subfolder)
            # Extend PATH so dependent DLLs can be discovered by the loader.
            os.environ['PATH'] = (
                dllabspath + os.pathsep + os.environ['PATH']
            )
            # Resolve DLL name via `find_library` to honor platform search rules.
            self._hsd_dll = cdll.LoadLibrary(util.find_library(dll_name))
        elif platform.system() == 'Darwin':
            # macOS: select arm64 or x86_64 dylib.
            if platform.machine() == 'arm64':
                base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                dll_name = os.path.join(
                    base,
                    "libhs_datalog",
                    "macos",
                    "arm64",
                    "libhs_datalog_v2.dylib",
                )
            elif platform.machine() == 'x86_64':
                base = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
                dll_name = os.path.join(
                    base,
                    "libhs_datalog",
                    "macos",
                    "x86_64",
                    "libhs_datalog_v2.dylib",
                )
            # Load Mach-O dynamic library.
            self._hsd_dll = cdll.LoadLibrary(dll_name)

        # Simplest callback type used by some registration APIs (no args).
        callaback_type = ctypes.CFUNCTYPE(None)

        # ---------------------
        # Hotplug notifications
        # ---------------------
        # Register two callbacks (plug/unplug). The DLL invokes them on USB
        # device arrival/removal.
        self.hs_datalog_register_usb_hotplug_callback = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_register_usb_hotplug_callback',
            ctypes.c_int,
            (callaback_type, callaback_type)
        )

        # -----------------
        # Open/Close the DLL
        # -----------------
        # Connection lifecycle with the HSD device.
        self.hs_datalog_open = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_open',
            ctypes.c_int,
            None
        )

        self.hs_datalog_close = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_close',
            ctypes.c_int,
            None
        )

        # -----------------------
        # Memory management (DLL)
        # -----------------------
        # Free memory allocated by the DLL and returned via char* out params.
        self.hs_datalog_free = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_free',
            ctypes.c_int,
            [ctypes.c_char_p]
        )

        # -----------------
        # Basic information
        # -----------------
        # Read-only queries: number of devices and DLL version.
        self.hs_datalog_get_device_number = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_device_number',
            ctypes.c_int,
            [ctypes.POINTER(ctypes.c_int)]
        )

        self.hs_datalog_get_version = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_version',
            ctypes.c_int,
            [ctypes.POINTER(ctypes.c_char_p)]
        )

        # ------------------------
        # Messaging and data plane
        # ------------------------
        # Send a PnPL command. Response is returned as len + char*.
        self.hs_datalog_send_message = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_send_message',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_char_p),
            ]
        )

        # Raw byte-stream data sending (if supported by device FW).
        # Note: Listed as missing implementation in higher-level layers.
        self.hs_datalog_send_data = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_send_data',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
        )

        # Data read API: first query available, then fetch bytes.
        self.hs_datalog_get_available_data_size = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_available_data_size',
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        )

        self.hs_datalog_get_data = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_data',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ]
        )

        # Board/firmware identity and presentation.
        self.hs_datalog_get_presentation = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_presentation',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        )

        # Only from HSD2 v >= 1.2.0
        self.hs_datalog_get_identity = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_identity',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        )

        # Device alias (string) getter/setter.
        self.hs_datalog_get_device_alias = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_device_alias',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        )

        self.hs_datalog_set_device_alias = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_device_alias',
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_char_p]
        )

        # Logging control (start/stop), RTC set, and JSON status getters/setters.
        self.hs_datalog_start_log = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_start_log',
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        )

        self.hs_datalog_stop_log = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_stop_log',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        )

        self.hs_datalog_set_rtc_time = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_rtc_time',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        )

        self.hs_datalog_get_device_status = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_device_status',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        )

        self.hs_datalog_set_device_status = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_device_status',
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_char_p]
        )

        # Component-level status and counts (total, sensors, algorithms).
        self.hs_datalog_get_component_status = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_component_status',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_char_p]
        )

        self.hs_datalog_get_components_number = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_components_number',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        )

        self.hs_datalog_get_sensor_components_number = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_sensor_components_number',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_bool]
        )

        self.hs_datalog_get_algorithm_components_number = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_algorithm_components_number',
            ctypes.c_int,
            [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_bool]
        )

        # ------------------
        # Property interfaces
        # ------------------
        # Boolean
        self.hs_datalog_get_boolean_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_boolean_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_bool),
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
        )

        self.hs_datalog_set_boolean_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_boolean_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_bool,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char_p),
            ]
        )

        # Integer
        self.hs_datalog_get_integer_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_integer_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
        )

        self.hs_datalog_set_integer_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_integer_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char_p),
            ]
        )

        # Float
        self.hs_datalog_get_float_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_float_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
        )

        self.hs_datalog_set_float_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_float_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_float,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char_p),
            ]
        )

        # String
        self.hs_datalog_get_string_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_get_string_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
        )

        self.hs_datalog_set_string_property = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_string_property',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char_p),
            ]
        )

        # Data-ready callback registration. The DLL invokes the provided
        # function pointer whenever new data is available for a component.
        # Note: Higher-level usage may be device/FW dependent.
        self.hs_datalog_set_data_ready_callback = wrap_hsd_function(
            self._hsd_dll,
            'hs_datalog_set_data_ready_callback',
            ctypes.c_int,
            [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.POINTER(
                    ctypes.CFUNCTYPE(
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.POINTER(ctypes.c_uint8),
                        ctypes.c_int,
                    )
                ),
            ]
        )

class HSD_Dll:
    """High-level Python API for HSDatalog device interaction.

    Wraps `HSD_Dll_Wrapper` to provide convenient methods for:
    - Device discovery/open/close.
    - PnPL messaging and property access (boolean/integer/float/string).
    - Logging control and RTC setting.
    - Data availability and reading.
    """

    def __init__(self):
        """Create the high-level wrapper and initialize callback pointers."""
        # Instantiate the low-level wrapper and prepare placeholders for
        # Python-side callback function pointers (to keep them alive).
        self.hsd_wrapper = HSD_Dll_Wrapper()
        self.plug_callaback_ptr = None
        self.unplug_callaback_ptr = None

    def hs_datalog_register_usb_hotplug_callback(self, plug_callback, unplug_callback) -> bool:
        """Register USB hotplug callbacks.

        Parameters:
        - plug_callback: callable invoked on device plug-in (no args).
        - unplug_callback: callable invoked on device unplug (no args).

        Returns:
        - bool: True on success, False if an `OSError` occurs.
        """
        try:
            # Build CFUNCTYPE wrappers around Python callables. Storing them on
            # `self` prevents garbage collection, which would invalidate the
            # native function pointer.
            callaback_type = ctypes.CFUNCTYPE(None)
            self.plug_callaback_ptr = callaback_type(plug_callback)
            self.unplug_callaback_ptr = callaback_type(unplug_callback)
            return (
                self.hsd_wrapper.hs_datalog_register_usb_hotplug_callback(
                    self.plug_callaback_ptr,
                    self.unplug_callaback_ptr,
                )
                == ST_HS_DATALOG_OK
            )
        except OSError:
            # Typically raised if the DLL cannot be loaded or the symbol is
            # missing. Return False to signal failure to the caller.
            return False

    def hs_datalog_open(self) -> bool:
        """Open a connection to the HSD device.

        Returns:
        - bool: True on success, False otherwise.
        """
        res = None
        try:
            res = self.hsd_wrapper.hs_datalog_open()
            return res == ST_HS_DATALOG_OK
        except OSError:
            print(
                f"{logger.get_datetime()} - HSDatalogApp.{__name__} - ERROR - "
                f"Error in opening the HSD device!"
            )
            return False

    def hs_datalog_close(self) -> bool:
        """Close the connection to the HSD device.

        Returns:
        - bool: True on success, False otherwise.
        """
        res = None
        try:
            res = self.hsd_wrapper.hs_datalog_close()
            return res == ST_HS_DATALOG_OK
        except OSError:
            print(
                f"{logger.get_datetime()} - HSDatalogApp.{__name__} - ERROR - "
                f"Error in closing the HSD device!"
            )
            return False

    def __hs_datalog_free(self, ptr) -> bool:
        """Free a native pointer allocated by the DLL.

        Parameters:
        - ptr: ctypes pointer returned by DLL functions.

        Returns:
        - bool: True on success; exits the process on failure.
        """
        res = self.hsd_wrapper.hs_datalog_free(ptr)
        if res != ST_HS_DATALOG_OK:
            # Some callers expect unrecoverable behavior on free failure.
            sys.exit("Error in memory free!")
        return res

    def hs_datalog_get_device_number(self) -> list[bool, int]:
        """Get the number of connected HSD devices.

        Returns:
        - list[bool,int]: `[success, device_count]`.
        """
        nof_devices = ctypes.c_int(0)
        return [
            self.hsd_wrapper.hs_datalog_get_device_number(ctypes.byref(nof_devices))
            == ST_HS_DATALOG_OK,
            nof_devices.value,
        ]

    def hs_datalog_get_version(self) -> list[str, int]:
        """Get the DLL version string.

        Returns:
        - list[str,int]: `[version_string, size]`.
        Notes:
        - Frees the returned pointer if present.
        """
        version_string = ctypes.c_char_p()
        version_sizeof = self.hsd_wrapper.hs_datalog_get_version(ctypes.byref(version_string))
        # vs = version_string.value.decode('UTF-8')
        # self.__hs_datalog_free(version_string)
        # return [vs, version_sizeof]
        raw = version_string.value
        if raw is None:
            vs = ""
        elif isinstance(raw, bytes):
            vs = raw.decode('UTF-8')
        else:
            vs = str(raw)
        if version_string and version_string.value:
            self.__hs_datalog_free(version_string)
            return [vs, version_sizeof]

    def hs_datalog_send_message(self, dId : int, msg : str, msg_len : int) -> list[bool, int, str]:
        """Send a PnPL message to the device.

        Parameters:
        - dId: int device id.
        - msg: str message payload (JSON or command string).
        - msg_len: int length of `msg`.

        Returns:
        - list[bool,int,str]: `[success, response_len, response_string]`.
        """
        dIdC = ctypes.c_int(dId)
        msgC = ctypes.c_char_p(msg.encode('UTF-8'))
        msg_lenC = ctypes.c_int(msg_len)
        data_lenC = ctypes.c_int(0)
        dataC = ctypes.c_char_p()
        res = self.hsd_wrapper.hs_datalog_send_message(
            dIdC,
            msgC,
            msg_lenC,
            ctypes.byref(data_lenC),
            ctypes.byref(dataC),
        )
        # Convert returned char* to Python str if present; otherwise provide a
        # success message. Always free the char* when allocated.
        if res != ST_HS_DATALOG_ERROR:
            if dataC.value is not None:
                data = (
                    dataC.value.decode('UTF-8')
                    if isinstance(dataC.value, (bytes, bytearray))
                    else str(dataC.value)
                )
                print(
                    f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                    f"PnPL Response: {data}"
                )
            else:
                data = "Command sent successfully!"
        else:
            return (False, 0, "[ERROR] - Command not sent correctly!")
        self.__hs_datalog_free(dataC)
        return (res == ST_HS_DATALOG_OK, data_lenC, data)

    def hs_datalog_get_available_data_size(self, dId : int, comp_name : str) -> list[bool, int]:
        """Get available bytes for a component's data stream.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.

        Returns:
        - list[bool,int]: `[success, bytes_available]`.
        """
        dIdC = ctypes.c_int(dId)
        comp_nameC = ctypes.c_char_p(comp_name.encode('utf-8'))
        available_dataC = ctypes.c_int(0)
        res = self.hsd_wrapper.hs_datalog_get_available_data_size(dIdC, comp_nameC, available_dataC)
        return ( res == ST_HS_DATALOG_OK, available_dataC.value)

    def hs_datalog_get_data(self, dId : int, comp_name : str, size : int) -> list[bool, bytes, int]:
        """Read a data block from a component.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.
        - size: int requested bytes.

        Returns:
        - list[bool,bytes,int]: `[success, data_bytes, actual_size]`.
        """
        dIdC = ctypes.c_int(dId)
        comp_nameC = ctypes.c_char_p(comp_name.encode('utf-8'))
        data_block = (ctypes.c_uint8 * size)()
        dataC = ctypes.cast(data_block, ctypes.POINTER(ctypes.c_uint8))
        sizeC = ctypes.c_int(size)
        actual_sizeC = ctypes.c_int(0)
        res = self.hsd_wrapper.hs_datalog_get_data(dIdC, comp_nameC, dataC, sizeC, actual_sizeC)
        # `string_at` copies the buffer into a bytes object of requested size.
        # Note: `actual_sizeC` may be smaller than `sizeC`; callers can use it
        # if needed for precise consumption.
        dataCcontent = ctypes.string_at(dataC,sizeC)
        return (res == ST_HS_DATALOG_OK, dataCcontent, sizeC.value)

    def hs_datalog_get_presentation(self, dId : int) -> list[bool, int, int]:
        """Get board and firmware identifiers.

        Parameters:
        - dId: int device id.

        Returns:
        - list[bool,int,int]: `[success, board_id, fw_id]`.
        """
        dIdC = ctypes.c_int(dId)
        bId = ctypes.c_int()
        fwId = ctypes.c_int()
        res = self.hsd_wrapper.hs_datalog_get_presentation(
            dIdC,
            ctypes.byref(bId),
            ctypes.byref(fwId),
        )
        return ( res == ST_HS_DATALOG_OK, bId.value, fwId.value)

    def hs_datalog_get_identity(self, dId : int) -> list[bool, int, int]:
        """Get board and firmware identifiers (HSD2 v >= 1.2.0).

        Parameters:
        - dId: int device id.

        Returns:
        - list[bool,int,int]: `[success, board_id, fw_id]`.
        """
        dIdC = ctypes.c_int(dId)
        bId = ctypes.c_int()
        fwId = ctypes.c_int()
        res = self.hsd_wrapper.hs_datalog_get_identity(
            dIdC,
            ctypes.byref(bId),
            ctypes.byref(fwId),
        )
        return ( res == ST_HS_DATALOG_OK, bId.value, fwId.value)

    def hs_datalog_get_device_alias(self, dId : int) -> list[bool, str]:
        """Get the device alias string.

        Parameters:
        - dId: int device id.

        Returns:
        - list[bool,str]: `[success, alias]`.
        """
        device_name = ctypes.c_char_p()
        dIdC = ctypes.c_int(dId)
        res = self.hsd_wrapper.hs_datalog_get_device_alias(dIdC, device_name)
        dname = (
            device_name.value.decode('UTF-8')
            if isinstance(device_name.value, (bytes, bytearray))
            else str(device_name.value)
        )
        # Free the DLL-allocated string buffer.
        self.__hs_datalog_free(device_name)
        return (res == ST_HS_DATALOG_OK, dname)

    def hs_datalog_set_device_alias(self, dId : int, alias : str) -> list[bool, str]:
        """Set the device alias.

        Parameters:
        - dId: int device id.
        - alias: str alias value.

        Returns:
        - bool: True on success, False otherwise.
        """
        dIdC = ctypes.c_int(dId)
        aliasC = ctypes.c_char_p(alias.encode('utf-8'))
        return self.hsd_wrapper.hs_datalog_set_device_alias(dIdC, aliasC) == ST_HS_DATALOG_OK

    def hs_datalog_start_log(self, dId : int, interface : int) -> bool:
        """Start logging on a given interface.

        Parameters:
        - dId: int device id.
        - interface: int interface id.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        cmd_response = ctypes.c_char_p()
        dIdC = ctypes.c_int(dId)
        interfaceC = ctypes.c_int(interface)
        cmd_res = ""
        res = self.hsd_wrapper.hs_datalog_start_log(
            dIdC,
            interfaceC,
            ctypes.byref(cmd_response),
        )
        try:
            if res != ST_HS_DATALOG_ERROR:
                if cmd_response.value is not None:
                    cmd_res = (
                        cmd_response.value.decode('UTF-8')
                        if isinstance(cmd_response.value, (bytes, bytearray))
                        else str(cmd_response.value)
                    )
                    print(
                        f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                        f"PnPL Response: {cmd_res}"
                    )
                else:
                    cmd_res = "Command sent successfully!"
            else:
                cmd_res = "Command failed"
        finally:
            # Ensure DLL-allocated response buffer is freed when present.
            if cmd_response and cmd_response.value:
                self.__hs_datalog_free(cmd_response)
        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_stop_log(self, dId : int) -> bool:
        """Stop logging.

        Parameters:
        - dId: int device id.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        cmd_response = ctypes.c_char_p()
        dIdC = ctypes.c_int(dId)
        cmd_res = ""
        res = self.hsd_wrapper.hs_datalog_stop_log(
            dIdC,
            ctypes.byref(cmd_response),
        )
        try:
            if res != ST_HS_DATALOG_ERROR:
                if cmd_response.value is not None:
                    cmd_res = (
                        cmd_response.value.decode('UTF-8')
                        if isinstance(cmd_response.value, (bytes, bytearray))
                        else str(cmd_response.value)
                    )
                    print(
                        f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                        f"PnPL Response: {cmd_res}"
                    )
                else:
                    cmd_res = "Command sent successfully!"
            else:
                cmd_res = "Command failed"
        finally:
            # Ensure DLL-allocated response buffer is freed when present.
            if cmd_response and cmd_response.value:
                self.__hs_datalog_free(cmd_response)
        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_set_rtc_time(self, dId : int) -> bool:
        """Set the device RTC time to the current host time.

        Parameters:
        - dId: int device id.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        cmd_response = ctypes.c_char_p()
        dIdC = ctypes.c_int(dId)
        cmd_res = ""
        res = self.hsd_wrapper.hs_datalog_set_rtc_time(
            dIdC,
            ctypes.byref(cmd_response),
        )
        try:
            if res != ST_HS_DATALOG_ERROR:
                if cmd_response.value is not None:
                    cmd_res = (
                        cmd_response.value.decode('UTF-8')
                        if isinstance(cmd_response.value, (bytes, bytearray))
                        else str(cmd_response.value)
                    )
                    print(
                        f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                        f"PnPL Response: {cmd_res}"
                    )
                else:
                    cmd_res = "Command sent successfully!"
            else:
                cmd_res = "Command failed"
        finally:
            # Ensure DLL-allocated response buffer is freed when present.
            if cmd_response and cmd_response.value:
                self.__hs_datalog_free(cmd_response)
        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_get_device_status(self, dId : int) -> list[bool, str]:
        """Get the device status JSON string.

        Parameters:
        - dId: int device id.

        Returns:
        - list[bool,str]: `[success, json_string]`.
        """
        device = ctypes.c_char_p()
        dIdC = ctypes.c_int(dId)
        res = self.hsd_wrapper.hs_datalog_get_device_status(
            dIdC,
            ctypes.byref(device),
        )
        if res != ST_HS_DATALOG_ERROR:
            d = (
                device.value.decode('UTF-8')
                if isinstance(device.value, (bytes, bytearray))
                else str(device.value)
            )
        else:
            return (False, None)
        # Free the DLL-allocated JSON string buffer.
        self.__hs_datalog_free(device)
        return (res == ST_HS_DATALOG_OK, d)

    def hs_datalog_set_device_status(self, dId : int, device_status : str) -> bool:
        """Set the device status from a JSON string.

        Parameters:
        - dId: int device id.
        - device_status: str JSON payload.

        Returns:
        - bool: True on success, False otherwise.
        """
        dIdC = ctypes.c_int(dId)
        dStatusC = ctypes.c_char_p(device_status.encode('utf-8'))
        return self.hsd_wrapper.hs_datalog_set_device_status(dIdC, dStatusC) == ST_HS_DATALOG_OK

    def hs_datalog_get_component_status(self, dId : int, comp_name : str) -> list[bool, str]:
        """Get a component status JSON string.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.

        Returns:
        - list[bool,str]: `[success, json_string]`.
        """
        component = ctypes.c_char_p()
        comp_nameC = ctypes.c_char_p(comp_name.encode('utf-8'))
        dIdC = ctypes.c_int(dId)
        res = self.hsd_wrapper.hs_datalog_get_component_status(
            dIdC,
            ctypes.byref(component),
            comp_nameC,
        )
        if res != ST_HS_DATALOG_ERROR:
            if component != '' and component.value is not None:
                try:
                    s = (
                        component.value.decode('UTF-8')
                        if isinstance(component.value, (bytes, bytearray))
                        else str(component.value)
                    )
                except UnicodeDecodeError:
                    return (False, None)
            else:
                return (False, None)
        else:
            return (False, None)
        # Free the DLL-allocated JSON string buffer.
        self.__hs_datalog_free(component)
        return (res == ST_HS_DATALOG_OK, s)

    def hs_datalog_get_components_number(self, dId : int) -> list[bool, int]:
        """Get total number of components.

        Parameters:
        - dId: int device id.

        Returns:
        - list[bool,int]: `[success, count]`.
        """
        dIdC = ctypes.c_int(dId)
        comp_number = ctypes.c_int()
        res = self.hsd_wrapper.hs_datalog_get_components_number(
            dIdC,
            ctypes.byref(comp_number),
        )
        return ( res == ST_HS_DATALOG_OK, comp_number.value)

    def hs_datalog_get_sensor_components_number(
        self,
        dId: int,
        only_enabled: bool,
    ) -> list[bool, int]:
        """Get number of sensor components.

        Parameters:
        - dId: int device id.
        - only_enabled: bool filter by enabled.

        Returns:
        - list[bool,int]: `[success, count]`.
        """
        dIdC = ctypes.c_int(dId)
        only_enabledC = ctypes.c_bool(only_enabled)
        comp_number = ctypes.c_int()
        res = self.hsd_wrapper.hs_datalog_get_sensor_components_number(
            dIdC,
            ctypes.byref(comp_number),
            only_enabledC,
        )
        return ( res == ST_HS_DATALOG_OK, comp_number.value)

    def hs_datalog_get_algorithm_components_number(
        self,
        dId: int,
        only_enabled: bool,
    ) -> list[bool, int]:
        """Get number of algorithm components.

        Parameters:
        - dId: int device id.
        - only_enabled: bool filter by enabled.

        Returns:
        - list[bool,int]: `[success, count]`.
        """
        dIdC = ctypes.c_int(dId)
        only_enabledC = ctypes.c_bool(only_enabled)
        comp_number = ctypes.c_int()
        res = self.hsd_wrapper.hs_datalog_get_algorithm_components_number(
            dIdC,
            ctypes.byref(comp_number),
            only_enabledC,
        )
        return ( res == ST_HS_DATALOG_OK, comp_number.value)

    def hs_datalog_get_boolean_property(
        self,
        dId: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> list[bool, bool]:
        """Read a boolean property.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - list[bool,bool]: `[success, value]`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_bool(0)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_get_boolean_property(
            dIdC,
            ctypes.byref(valueC),
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
        )
        return (res == ST_HS_DATALOG_OK, valueC.value)

    def hs_datalog_set_boolean_property(
        self,
        dId: int,
        value: bool,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> bool:
        """Set a boolean property.

        Parameters:
        - dId: int device id.
        - value: bool property value.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_bool(value)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        cmd_response = ctypes.c_char_p()
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_set_boolean_property(
            dIdC,
            valueC,
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
            ctypes.byref(cmd_response),
        )
        if res != ST_HS_DATALOG_ERROR:
            if cmd_response.value is not None:
                cmd_res = (
                    cmd_response.value.decode('UTF-8')
                    if isinstance(cmd_response.value, (bytes, bytearray))
                    else str(cmd_response.value)
                )
                print(
                    f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                    f"PnPL Response: {cmd_res}"
                )
            else:
                cmd_res = "Command sent successfully!"
        else:
            return (False, 0, "[ERROR] - Command not sent correctly!")

        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_get_integer_property(
        self,
        dId: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> list[bool, int]:
        """Read an integer property.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - list[bool,int]: `[success, value]`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_int(0)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        return (
            self.hsd_wrapper.hs_datalog_get_integer_property(
                dIdC,
                ctypes.byref(valueC),
                comp_nameC,
                prop_nameC,
                sub_prop_nameC,
            )
            == ST_HS_DATALOG_OK,
            valueC.value,
        )

    def hs_datalog_set_integer_property(
        self,
        dId: int,
        value: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> bool:
        """Set an integer property.

        Parameters:
        - dId: int device id.
        - value: int property value.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_int(value)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        cmd_response = ctypes.c_char_p()
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_set_integer_property(
            dIdC,
            valueC,
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
            ctypes.byref(cmd_response),
        )
        if res != ST_HS_DATALOG_ERROR:
            if cmd_response.value is not None:
                if isinstance(cmd_response.value, (bytes, bytearray)):
                    cmd_res = cmd_response.value.decode('UTF-8')
                else:
                    cmd_res = str(cmd_response.value)
                print(
                    f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                    f"PnPL Response: {cmd_res}"
                )
            else:
                cmd_res = "Command sent successfully!"
        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_get_float_property(
        self,
        dId: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> list[bool, float]:
        """Read a float property.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - list[bool,float]: `[success, value]`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_float(0)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        return (
            self.hsd_wrapper.hs_datalog_get_float_property(
                dIdC,
                ctypes.byref(valueC),
                comp_nameC,
                prop_nameC,
                sub_prop_nameC,
            )
            == ST_HS_DATALOG_OK,
            valueC.value,
        )

    def hs_datalog_set_float_property(
        self,
        dId: int,
        value: float,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> bool:
        """Set a float property.

        Parameters:
        - dId: int device id.
        - value: float property value.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_float(value)
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        cmd_response = ctypes.c_char_p()
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_set_float_property(
            dIdC,
            valueC,
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
            ctypes.byref(cmd_response),
        )
        if res != ST_HS_DATALOG_ERROR:
            if cmd_response.value is not None:
                if isinstance(cmd_response.value, (bytes, bytearray)):
                    cmd_res = cmd_response.value.decode('UTF-8')
                else:
                    cmd_res = str(cmd_response.value)
                print(
                    f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                    f"PnPL Response: {cmd_res}"
                )
            else:
                cmd_res = "Command sent successfully!"
        return (res == ST_HS_DATALOG_OK, cmd_res)

    def hs_datalog_get_string_property(
        self,
        dId: int,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None,
    ) -> list[bool, str]:
        """Read a string property.

        Parameters:
        - dId: int device id.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - list[bool,str]: `[success, value]`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_char_p()
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_get_string_property(
            dIdC,
            ctypes.byref(valueC),
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
        )
        if isinstance(valueC.value, (bytes, bytearray)):
            value = valueC.value.decode('UTF-8')
        else:
            value = str(valueC.value)
        # Free the DLL-allocated string buffer.
        self.__hs_datalog_free(valueC)
        return (res == ST_HS_DATALOG_OK, value)

    def hs_datalog_set_string_property(
        self,
        dId: int,
        value: str,
        comp_name: str,
        prop_name: str,
        sub_prop_name: str | None = None,
    ) -> bool:
        """Set a string property.

        Parameters:
        - dId: int device id.
        - value: str property value.
        - comp_name: str component name.
        - prop_name: str property name.
        - sub_prop_name: Optional[str] nested property name.

        Returns:
        - tuple(bool,str): `(success, response_string)`.
        """
        dIdC = ctypes.c_int(dId)
        valueC = ctypes.c_char_p(value.encode('UTF-8'))
        comp_nameC = ctypes.c_char_p(comp_name.encode('UTF-8'))
        prop_nameC = ctypes.c_char_p(prop_name.encode('UTF-8'))
        cmd_response = ctypes.c_char_p()
        if sub_prop_name is None:
            sub_prop_nameC = None
        else:
            sub_prop_nameC = ctypes.c_char_p(sub_prop_name.encode('UTF-8'))
        res = self.hsd_wrapper.hs_datalog_set_string_property(
            dIdC,
            valueC,
            comp_nameC,
            prop_nameC,
            sub_prop_nameC,
            ctypes.byref(cmd_response),
        )
        if res != ST_HS_DATALOG_ERROR:
            if cmd_response.value is not None:
                if isinstance(cmd_response.value, (bytes, bytearray)):
                    cmd_res = cmd_response.value.decode('UTF-8')
                else:
                    cmd_res = str(cmd_response.value)
                print(
                    f"{logger.get_datetime()} - HSDatalogApp.{__name__} - INFO - "
                    f"PnPL Response: {cmd_res}"
                )
            else:
                cmd_res = "Command sent successfully!"
        return (res == ST_HS_DATALOG_OK, cmd_res)

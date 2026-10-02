---
pagetitle: Release Notes for stdatalog_core 
lang: en
header-includes: <link rel="icon" type="image/x-icon" href="_htmresc/favicon.png" />
---

::: {.row}
::: {.col-sm-12 .col-lg-4}

<center> 
# Release Notes for <mark>stdatalog_core</mark> 
Copyright &copy; 2025 STMicroelectronics
    
[![ST logo](_htmresc/st_logo_2020.png)](https://www.st.com){.logo}
</center>


# Purpose

The **[stdatalog_core](https://github.com/STMicroelectronics/stdatalog_core)** package is designed for high-speed data logging and communication with STMicroelectronics hardware devices. It provides a comprehensive set of tools for data acquisition, processing, conversion, and visualization.
It manages USB communication to retrieve connected board information and data, set target properties, and control the data acquisition process. Additionally, it oversees error management and application log messages, ensuring smooth and reliable operation.

The package is part of the **[STDATALOG-PYSDK](https://github.com/STMicroelectronics/stdatalog-pysdk)**, which is a set of libraries and tools that enable the development of applications for data logging and data monitoring.

:::

::: {.col-sm-12 .col-lg-8}
# Update History

::: {.collapse}
<input type="checkbox" id="collapse-section6" checked aria-hidden="true">
<label for="collapse-section6" aria-hidden="true">v1.5.0 / 18-Sep-26</label>
<div>


## Main Changes

### Maintenance Release

- Optimized data streaming: precompute constants for the zero-copy multi-packet parsing (zero-copy DataReader)
- Fixed offline converters and plot for ToF (generic output_format)
- Added support to Python 3.14
- Updated requirements with more recent versions of packages dependencies


</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section6" aria-hidden="true">
<label for="collapse-section6" aria-hidden="true">v1.4.0 / 15-May-26</label>
<div>


## Main Changes

### Maintenance Release

- Added support for uploading JSON configuration files for MLC and ISPU sensors
  - Renamed attributes
  - Updated methods to use MLC/ISPU formats with JSON and maintained retrocompatibility with UCF format
  - Fixed plot function + dataframe conversion for ISPU 
- Refactored utility modules for improved documentation and code clarity
  - Enhanced docstrings across `file_manager.py`, `plot_utils.py`, `sensors_utils.py`, and `type_conversion.py` to provide clearer descriptions of module purposes, function parameters, return types, and usage examples.
  - Improved code readability by standardizing formatting, including consistent use of whitespace and line breaks.
  - Updated `FileManager` methods to clarify file naming conventions and error handling.
  - Refined `PlotUtils` methods for drawing regions and tags on Plotly figures, including better parameter descriptions and behavior notes.
  - Streamlined `SensorUtils` and `TypeConversion` classes for sensor type management and type conversion, respectively, with clearer logic and reduced branching.
  - Added error handling and edge case considerations in type conversion functions, ensuring robustness against unexpected inputs.
- Refactored HSDatalog_v2 
  - Added support streaming Parquet row-groups with fastparquet and fallback to (lazy) Dask DataFrame if available
  - Improved type casting and rounding for data columns: solved "ispu" casting
- Added get_type_short function in sensors_utils.py
- Added missing nullity check for dataframe variable in __get_dataframe_batch and __get_dataframe_batch_gen methods
- Refactored HSDatalog to streamline sensor and actuator data handling in HDF5 format
- Improve command response handling in PnPLSTSRL_CommandManager
- Refactored SSTL implementation and enhance documentation
  - Updated SSTL.py to improve code structure and readability.
  - Added comprehensive docstrings for classes and methods in SSTL.
  - Implemented detailed comments explaining packet header construction and segmentation logic.
  - Modified STWIN_HSD communication files to skip pylint checks for better compatibility.
  - Enhanced STWINHSDSetMLCSensorCmd class with additional documentation for clarity.
  - Ensured consistent formatting and style across communication modules.
- Improved data loss detection over ASPEP serial via CRC
- Added dedicated exceptions for SSTL and PnPLSTSRL_com_manager
- Fixed exception_error, when closing main window
- Enhanced documentation and error handling across modules
  - Updated docstrings in converters.py to provide detailed descriptions of classes and methods, including usage examples and parameter explanations.
  - Improved error handling in dtm.py for loading Device Template Models, ensuring better validation and logging of issues.
  - Expanded exception classes in exceptions.py with detailed docstrings, providing context for each error type and improving clarity for users.
  - Enhanced logging functionality in logger.py with improved formatting and documentation for better usability and understanding of logging behavior.
- Replaced pyarrow with fastparquet for DataFrame to Parquet conversion

</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section5" aria-hidden="true">
<label for="collapse-section5" aria-hidden="true">v1.3.0 / 15-Nov-25</label>
<div>


## Main Changes

### Maintenance Release and Product Update

- **Full support to Serial Datalog adding compatibility with STSW-SDATALOG firmware examples:**
  - Reshaped serial communication protocol management, based on ASPEP and SSTL
  - Manage TX and RX segmented packages
  - Fixded bit order for beacon messages and packet_number
  - Reset all serial connection attributes in PnPLSTSRL_CommandManager close() function + updated flush() function
  - Added timeout and data len check
- Added get_base_acquisition_folder
- Added missing cmd response check in hs_datalog_stop_log and hs_datalog_set_rtc_time
- Fixed dimensions for "ispu" use case: calculate it instead of hard coded value
- Robust tags extraction from acquisition info model status
- **Optimized convert_acquisition_to_hdf5 function**
  - Added parameter "target_filename=None" to function convert_acquisition_to_hdf5 if user want to rename the h5 file created
  - Manage missing tags_info component
- Bug fixing and code cleaning. Fixed typos


</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section4" aria-hidden="true">
<label for="collapse-section4" aria-hidden="true">v1.2.1 / 29-Aug-25</label>
<div>


## Main Changes

### Patch Release

- Fixed to_parquet and h5 converter and plot functions
- Updated HSDLink start_log API


</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section3" aria-hidden="true">
<label for="collapse-section3" aria-hidden="true">v1.2.0 / 20-Jun-25</label>
<div>


## Main Changes

### Maintenance Release

- Added support to Python 3.13 
- Removed dependency from matplotlib: use Parquet + Plotly (wResampler) + Dash as default for HSDatalog_v2 plots
- Updated type_conversion, managing float_t, double_t, int_24_t, uint24_t
- Updated ACTUATOR components and properties management
- Updated ToF plot: added ranging distance value for each zone + automatic time scrolling animation
- New USB catalog management: sync/update with the online catalog
- Moved query_dtdl_model from DeviceTemplateManager to DeviceCatalogManager class
- Fixed data conversion in batch with dummy data (or spts = 0)
- Fixed empty conversion job from not supported algorithm (i.e.: ai_motor_classifier)
- Integration of new staiotcraft_sdk library version


</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section2" aria-hidden="true">
<label for="collapse-section2" aria-hidden="true">v1.1.0 / 9-Apr-25</label>
<div>


## Main Changes

### Maintenance Release

- **Added macos support.**
- **Updated libhs_datalog_v2 libraries for all the supported OS.**
	- Updated libusb linking and added missing lirbary import for UNIX.
	- Added a new logging system to manage application messages with different levels (NONE, ERROR, WARNING, INFO, DEBUG).
	- Updated cmake_minimum_required version used to recompile libraries.
	- Added new hs_datalog_load_ucf_file_to_mlc API (deprecated old hs_datalog_load_ucf_to_mlc API).
- Added hs_datalog_close error message.
- Added support for Vanilla and serial datalogger.
- Updated return values for last_index, missing_bytes and saved_bytes when returning from__extract_data in case of EOF and fixed missing len(df) check.


</div>
:::

::: {.collapse}
<input type="checkbox" id="collapse-section1" aria-hidden="true">
<label for="collapse-section1" aria-hidden="true">v1.0.0 / 17-Jan-25</label>
<div>


## Main Changes

### First official release


</div>
:::

:::
:::

<footer class="sticky">
::: {.columns}
::: {.column width="95%"}
For complete documentation,
visit: [www.st.com](https://github.com/STMicroelectronics/stdatalog-pysdk)
:::
::: {.column width="5%"}
<abbr title="Based on template cx566953 version 2.0">Info</abbr>
:::
:::
</footer>

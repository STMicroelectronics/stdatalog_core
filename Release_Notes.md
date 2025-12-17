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
<input type="checkbox" id="collapse-section5" checked aria-hidden="true">
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

# stdatalog_core

![latest tag](https://img.shields.io/github/v/tag/STMicroelectronics/stdatalog_core.svg?color=brightgreen)

The **[stdatalog_core](https://github.com/STMicroelectronics/stdatalog_core)** package is designed for high-speed data logging and communication with STMicroelectronics hardware devices. It provides a comprehensive set of tools for data acquisition, processing, conversion, and visualization.
It manages USB communication to retrieve connected board information and data, set target properties, and control the data acquisition process. Additionally, it oversees error management and application log messages, ensuring smooth and reliable operation.

The package is part of the **[STDATALOG-PYSDK](https://github.com/STMicroelectronics/stdatalog-pysdk)**, which is a set of libraries and tools that enable the development of applications for data logging and data monitoring.

This package is composed of three sub-packages (The acronym HSD stands for High-Speed Data Logger):
- HSD: This package contains the core classes and functions to manage HSD acquisition folders. Its main features are listed below:
    - Validate HSD folder and determine version
    - Retrieve device, sensors (and other component types), acquisition and firmware information
    - Get and filter acquired data and timestamps
    - Extract data as dataframes and convert to various formats (CSV, TSV, TXT, APACHE PARQUET, HDF5, WAV)
    - Plot acquired sensor, algorithm and/or telemetries from actuators data
- HSDLink: This package contains the classes and functions to manage the communication with a connected board. Its main features are listed below:
    - Device discovery and connection
    - Set target properties, send commands and control the data acquisition process
    - Retrieve board components information
    - Retrieve and save data from the board components (sensors, actuators, algorithms, telemetries, informative components, ...)
    - Assign tag labels to acquired data
    - Smart sensors programming and data acquisition
- HSD_utils: This package contains utility functions to manage HSD data and folders. Its main features are listed below:
    - Application logging and error management
    - Data conversion functions for converting data to various formats (CSV, TSV, TXT, APACHE PARQUET, HDF5, WAV)
    - Extract data from raw interleaved data+timestamp buffers
    - Helper functions for common tasks such as string formatting, date handling, and list management.

The package is compatible with Windows, Linux and macOS, and supports Python 3.10 to 3.13.

## Features

- High-speed data logging
- Support for multiple platforms (Windows, Linux, macOS)
- Compatible with Python 3.10 to 3.13
- Communication with various hardware devices
- Data processing, conversion and visualization utilities

## Installation

To install the `stdatalog_core` package after downloading it, execute the following command from the package's root directory:
NOTE: Be sure to satisfy the requirements before installing the package ([see Requirements](#requirements)).

On Windows:
```sh
python -m pip install dist\stdatalog_core-1.2.0-py3-none-any.whl
```

On Linux/macOS:
```sh
python3 -m pip install dist/stdatalog_core-1.2.0-py3-none-any.whl
```

The package could also be installed as part of the **[STDATALOG-PYSDK](https://github.com/STMicroelectronics/stdatalog-pysdk)** by launching the SDK installation script from the SDK root folder:

On Windows:
```sh
.\STDATALOG-PYSDK_install.bat
```

On Linux/macOS:
```sh
./STDATALOG-PYSDK_install.sh
```

Source code is also available within the inner `stdatalog_core` folder.

## Requirements
The package requires the following dependencies:

- **[stdatalog_pnpl](https://github.com/STMicroelectronics/stdatalog_pnpl)**
- numpy==2.2.4
- pyserial==3.5
- pandas==2.2.3
- h5py==3.13.0
- colorama==0.4.6
- click==8.1.8
- setuptools<81
- dask==2025.5.1
- pyarrow==20.0.0
- plotly_resampler==0.10.0
- python-statemachine==2.5.0

## Usage
Here is a basic example of how to use the stdatalog_core package:

### How to use HSDatalog class:
```python
from stdatalog_core.HSD.HSDatalog import HSDatalog

# change the "path/to/your/acquisition_folder" with the path of the acquisition folder you want to analyze
acquisition_folder = "path/to/your/acquisition_folder"

# Create an instance of HSDatalog
hsd = HSDatalog()

# Validate the HSD folder and determine the version
hsd_version = hsd.validate_hsd_folder(acquisition_folder)
print(f"HSD Version: {hsd_version}\n")

# Create the appropriate HSDatalog instance based on the folder content
hsd_instance = hsd.create_hsd(acquisition_folder=acquisition_folder)

# Get device information
device_info = hsd.get_device_info(hsd_instance)

# --> For other functionalities, please refer to the HSDatalog class documentation
```
You can find the `HSDatalog.py` file **[here](https://github.com/STMicroelectronics/stdatalog_core/blob/main/stdatalog_core/HSD/HSDatalog.py)**. For a complete reference on how to use the `HSDatalog` class, refer to the **[stdatalog_API_examples_HSDatalog.py](https://github.com/STMicroelectronics/stdatalog_examples/blob/main/function_tests/stdatalog_API_examples_HSDatalog.py)** example or to the **[nb_stdatalog_core.ipynb](https://github.com/STMicroelectronics/stdatalog_examples/blob/main/how-to_notebooks/nb_stdatalog_core.ipynb)** Jupyter Notebook in the STDATALOG-PYSDK examples folder.

### How to use HSDLink class:
NOTE: It is necessary to connect a compatible device (board flashed with FP-SNS-DATALOG2 or FP-IND-DATALOGMC) to the PC to run the script.
```python
from stdatalog_core.HSD_link.HSDLink import HSDLink

# change the "path/to/your/acquisition_folder" with the path in which the acquisition folder will be saved
acquisition_folder = "path/to/your/acquisition_folder"

# Create an instance of HSDLink
hsd_link = HSDLink()

# Create the appropriate HSDLink instance based on the connected board
hsd_link_instance = hsd_link.create_hsd_link(dev_com_type='st_hsd', acquisition_folder=acquisition_folder)

if hsd_link is None:
    print("No compatible devices connected.")
    return

# Get the version of the HSDLink instance
version = hsd_link.get_version(hsd_link_instance)

# --> For other functionalities, please refer to the HSDLink class documentation
```
You can find the `HSDLink.py` file **[here](https://github.com/STMicroelectronics/stdatalog_core/blob/main/stdatalog_core/HSD_link/HSDLink.py)**. For a complete reference on how to use the `HSDLink` class, refer to the **[stdatalog_API_examples_HSDLink.py](https://github.com/STMicroelectronics/stdatalog_examples/blob/main/function_tests/stdatalog_API_examples_HSDLink.py)** example or to the **[nb_stdatalog_communication.ipynb](https://github.com/STMicroelectronics/stdatalog_examples/blob/main/how-to_notebooks/nb_stdatalog_communication.ipynb)** Jupyter Notebook in the STDATALOG-PYSDK examples folder.

## License
This software is licensed under the BSD 3-clause license. See the LICENSE.md file for more details.

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

import setuptools

with open("LICENSE.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="stdatalog_core",
    version="1.5.0",
    author="SRA-ASP",
    author_email="matteo.ronchi@st.com",
    description="STMicroelectronics High Speed Datalog python package",
    long_description=long_description,
    long_description_content_type="text\\markdown",
    include_package_data=True,
    url="https://github.com/STMicroelectronics/stdatalog_core",
    packages=setuptools.find_packages(),
    package_dir={'stdatalog_core': 'stdatalog_core'},
    license='BSD 3-clause',
    classifiers=[
        "License :: BSD License (BSD-3-Clause)",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: Linux",
        "Operating System :: MacOS", 
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Embedded Systems"
    ],
    install_requires=[
        "stdatalog_pnpl==1.5.0",
        "numpy==2.5.2",
        "pyserial==3.5",
        "pandas==3.0.5",
        "h5py==3.16.0",
        "colorama==0.4.6",
        "click==8.4.2",
        "setuptools==84.0.0",
        "dask==2026.7.1",
        "fastparquet==2026.5.0",
        "plotly_resampler==0.11.0",
        "python-statemachine==3.2.1"
    ]
)
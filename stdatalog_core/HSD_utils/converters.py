# *****************************************************************************
#  * @file    converters.py
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
Utilities to convert and export time-series data from pandas DataFrames into
standard and specialized formats used by STMicroelectronics tools.
Contents:

- NanoedgeCSVWriter
    - Writes rolling-window signals to a NanoEdge-compatible CSV.
    - Supports signal_length and signal_increment to control window size and stride.
    - Handles partial batches across calls via internal buffering.
    - Optional target_value prepended to each signal row.
    - flush() writes any remaining buffered values.

- HSDatalogConverter (static methods)
    - to_txt / to_csv / to_tsv: Export DataFrame to delimited text using pandas.
    - to_parquet: Export a single chunk to Parquet via fastparquet (no append mode).
    - to_xsv_numpy: Export using numpy.savetxt with dtype-aware formatting.
    - to_xsv: Generic delimited export via pandas.to_csv with performance-oriented string casting.
    - WAV helpers:
        - to_wav: Deprecated single-shot PCM→WAV export.
        - wav_create / wav_append / wav_close: Streamed WAV creation and finalization.
    - to_unico: Export to ST Unico formats (txt/csv/tsv), optionally dropping Time columns
        and applying label schemes (e.g., "mlc_tool").
    - rename_dataframe_columns: Rename columns based on component names and label schemes,
        excluding time and optional tag columns.
    - merge_dataframes: As-of merge on 'Time' across multiple DataFrames, with optional renaming.

- When signal_increment <= 0, it defaults to signal_length; negative values trigger a warning.
- to_parquet must be called per chunk (no append); merge results externally if needed.
- merge_dataframes uses pandas.merge_asof on the 'Time' column after converting it to float.
- External dependencies: numpy, pandas, fastparquet, wave, csv, and a module-level logger.
"""

import os
import warnings
import csv
import wave
import numpy as np
import pandas as pd
import fastparquet
_PARQUET_ENGINE = "fastparquet"
import time

import stdatalog_core.HSD_utils.logger as logger
from stdatalog_core.HSD_utils.exceptions import NanoEdgeConversionError

log = logger.get_logger(__name__)

class NanoedgeCSVWriter:
    """
        Utility class to write data in NanoEdge CSV format.
        Methods:
            - to_nanoedge_format_batch: writes data from a DataFrame to CSV in Nano
                Edge format, handling signal length and increment.
            - flush: writes any remaining buffered data to the CSV file.
    Notes:
        - The class manages an internal buffer to accumulate data before writing.
    """

    def __init__(self, output_folder, comp_name, signal_length, signal_increment=0):
        """
        Initializes the NanoedgeCSVWriter with output folder, component name,
        signal length, and signal increment.
        Parameters:
            - output_folder: Directory where the CSV file will be saved.
            - comp_name: Name of the component (used in the filename).
            - signal_length: Number of samples per signal to write.
            - signal_increment: Number of samples to skip between signals.
                                If <= 0, it defaults to signal_length.
        """
        self.signal_length = signal_length
        self.signal_increment = signal_increment
        if self.signal_increment <= 0:
            if self.signal_increment < 0:
                log.warning(
                    (
                        "You chose a negative value "
                        f"[{self.signal_increment}] for 'signal_increment'. "
                        "Using signal_length for this execution."
                    )
                )
            self.signal_increment = self.signal_length

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        filename = (
            f"{comp_name}_" + os.path.basename(os.path.normpath(output_folder)) + "_NanoEdge.csv"
        )
        self.file_path = os.path.join(output_folder, filename)

        self.buffer = []
        self.samples_chest = np.array([])
        self.start_idx = 0

    def to_nanoedge_format_batch(self, dataframe, mode="w", target_value=None):
        """
        Writes the contents of a NumPy array to a CSV file with a fixed number of columns (M).
        The array's values are written consecutively without padding, filling up rows as needed.

        Parameters:
        - input_array: A NumPy array to be written to the CSV.
        """
        # arrange data for nanoedge
        filtered_df = dataframe.drop("Time", axis=1)
        dataset = filtered_df.to_numpy()

        if len(self.samples_chest) != 0:
            dataset = np.vstack((self.samples_chest, dataset))

        idx = self.start_idx
        # Number of rows extractable from the array (taking into account of signal_increment)
        # e.g.
        # - dataset_length (samples) = 10 [1,2,3,4,5,6,7,8,9,10]
        # - signal_length (samples) = 4
        # - signal_increment (samples) = 2
        # --> output:
        # ((dataset_length - signal_length) // signal_increment) + 1
        # = ((10 - 4) // 2) + 1 = 4
        #  1, 2, 3, 4   ┐
        #  3, 4, 5, 6   | n_signals = 4
        #  5, 6, 7, 8   |
        #  7, 8, 9, 10  ┘
        #  8, 10, X, X
        n_signals = ((np.shape(dataset[idx:])[0] - self.signal_length) // self.signal_increment) + 1

        with open(self.file_path, mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for _ in range(0, n_signals):  # rows of final dataset
                if target_value is not None:
                    self.buffer.append(target_value)
                for cc in range(idx, idx + self.signal_length):  # cc = columns in input dataset
                    if cc >= dataset.shape[0]:
                        continue
                    for el in dataset[cc]:
                        self.buffer.append(el)

                idx += self.signal_increment
                writer.writerow(list(self.buffer))
                self.buffer.clear()

        if n_signals > 0:
            a = self.signal_length - self.signal_increment
            if a >= 0:  # To manage: signal_increment >= signal_length
                try:
                    self.samples_chest = dataset[(cc + 1) - (a) :]
                except Exception:
                    pass
            else:  # To manage: signal_increment > signal_length
                try:
                    self.samples_chest = dataset[(cc + 1) :]
                    self.start_idx = -a
                except Exception:
                    pass

        return True

    def flush(self):
        """
        Writes any remaining values in the buffer to the CSV file as a partial row.
        This should be called after all arrays have been processed.
        """
        if self.buffer:
            with open(self.file_path, "a", newline="", encoding="utf-8") as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow(self.buffer)
            self.buffer = []


class HSDatalogConverter:
    """
    Utility class for converting DataFrames to various file formats.
    Methods:
        - to_txt: Converts a DataFrame to a TXT (tab-separated) file.
        - to_csv: Converts a DataFrame to a CSV file.
        - to_tsv: Converts a DataFrame to a TSV file.
        - to_parquet: Converts a DataFrame to a Parquet file.
        - to_xsv_numpy: Converts a DataFrame to a delimited text file using NumPy.
        - to_xsv: Converts a DataFrame to a delimited text file using pandas.
        - to_wav: Converts PCM data to a WAV file (deprecated).
        - wav_create: Creates a new WAV file.
        - wav_append: Appends PCM data to an open WAV file.
        - wav_close: Closes the WAV file.
        - to_unico: Writes a DataFrame to a file in a specified ST Unico format.
        - rename_dataframe_columns: Renames columns based on component names
            and labeling schemes.
        - merge_dataframes: Merges by 'Time' and renames columns.
    """

    @staticmethod
    def to_txt(df, filename, mode="w"):
        """
        Converts a DataFrame to a TXT (tab-separated) file.

        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        HSDatalogConverter.to_xsv(df, filename, ".txt", "\t", mode)

    @staticmethod
    def to_csv(df, filename, mode="w"):
        """
        Converts a DataFrame to a CSV file.

        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        # Delegate the conversion to the generic 'to_xsv' method for CSV format.
        HSDatalogConverter.to_xsv(df, filename, ".csv", ",", mode)
        # HSDatalogConverter.my_to_xsv(df, filename, '.csv', ',', mode)

    @staticmethod
    def to_tsv(df, filename, mode="w"):
        """
        Converts a DataFrame to a TSV file.

        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        # Delegate the conversion to the generic 'to_xsv' method for TSV format.
        HSDatalogConverter.to_xsv(df, filename, ".tsv", "\t", mode)

    @staticmethod
    def to_parquet(df: pd.DataFrame, filename):
        """
        Write a DataFrame to a Parquet file using fastparquet via pandas.
        This function writes a single Parquet file. If you need multiple chunks,
        call this function per chunk and manage concatenation externally.
                
        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        """
        parquet_file_path = filename if filename.endswith(".parquet") else filename + ".parquet"
        if _PARQUET_ENGINE is None:
            raise RuntimeError("fastparquet not installed. Please run: pip install fastparquet")
        # Ensure numeric types are preserved; index disabled
        df.to_parquet(parquet_file_path, engine=_PARQUET_ENGINE, index=False)
    @staticmethod
    def to_xsv_numpy(df, filename, extension, separator, mode="w"):
        """
        Converts a DataFrame to a delimited text file (e.g., CSV, TSV) using NumPy's savetxt method.

        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        :param extension: The file extension to use (e.g., '.csv' for CSV files).
        :param separator: The delimiter to use between values (e.g., ',' for CSV).
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        # Construct the full file path by appending the extension to the filename.
        filepath = filename + extension
        # Determine whether to write headers based on the file mode and existence of the file.
        header = True  # Default is to write headers.
        if mode == "a" and os.path.exists(filename + extension):
            # If appending to an existing file, do not write headers.
            header = False
        # Define the format for each column based on its data type.
        formats = [
            "%s" if dtype == object else "%d" if dtype == np.int_ else "%.12g"
            for dtype in df.dtypes
        ]
        # Open the file with the specified mode and write the DataFrame content.
        with open(filepath, mode, encoding="utf-8") as f:
            # Write the header if required.
            if header:
                f.write(separator.join(df.columns) + "\n")
            # Write the DataFrame using NumPy's savetxt with given formats and delimiter.
            np.savetxt(f, df.values, delimiter=separator, fmt=formats, comments="")

    @staticmethod
    def to_xsv(df, filename, extension, separator, mode="w"):
        """
        Converts a DataFrame to a delimited text file (e.g., CSV, TSV) using pandas to_csv method.

        :param df: The DataFrame to convert.
        :param filename: The base name of the file to write to.
        :param extension: The file extension to use.
        :param separator: The delimiter to use between values.
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        # Convert all columns to strings for consistent formatting and faster conversion.
        df = df.astype({col: "str" for col in df.columns})
        # Use pandas to_csv to write the DataFrame with the specified delimiter and mode.
        df.to_csv(
            filename + extension,
            sep=separator,
            mode=mode,
            compression=None,
            index=False,
            header=True if mode == "w" else False,
            float_format="%.6f",
        )
        log.debug(
            f'--> File: "{filename + extension}" converted chunk appended successfully'
        )

    @staticmethod
    def to_wav(pcm_data, filename, sample_freq):
        """
        Converts PCM data to a WAV file.

        DEPRECATED: This method will be removed in a future version.
        Use 'wav_create', 'wav_append', and 'wav_close' for more control over WAV file creation.

        :param pcm_data: The PCM data to convert.
        :param filename: The name of the WAV file to create.
        :param sample_freq: The sample frequency of the audio data.
        """
        # Issue a deprecation warning to the user.
        warnings.warn(
            "The 'to_wav' method is deprecated and will be removed in a future version. "
            "Use 'wav_create', 'wav_append', and 'wav_close' methods instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        wav_file = wave.open(filename, mode="wb")
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_freq)
        wav_file.writeframesraw(bytearray(pcm_data))
        log.info(f'--> Wav file: "{filename}" correctly exported')

    @staticmethod
    def wav_create(filename, sample_freq, nchannels=1):
        """
        Creates a new WAV file and sets its parameters.

        :param filename: The name of the WAV file to create.
        :param sample_freq: The sample frequency of the audio data.
        :param nchannels: The number of channels in the audio data (default is 1).
        :return: The wave.Wave_write object for the created WAV file.
        """
        # Open a new WAV file for writing.
        wav_file = wave.open(filename, mode="wb")
        # Set the WAV file parameters.
        wav_file.setnchannels(nchannels)
        wav_file.setsampwidth(2)  # Assuming 16-bit samples (2 bytes per sample).
        wav_file.setframerate(sample_freq)
        # Return the wave.Wave_write object to allow appending data later.
        return wav_file

    @staticmethod
    def wav_append(wav_file, pcm_data):
        """
        Appends PCM data to an open WAV file.

        :param wav_file: The wave.Wave_write object representing the open WAV file.
        :param pcm_data: The PCM data to append to the WAV file.
        """
        # Write the raw PCM data to the WAV file.
        wav_file.writeframesraw(bytearray(pcm_data))

    @staticmethod
    def wav_close(wav_file):
        """
        Closes the WAV file and logs the successful export.

        :param wav_file: The wave.Wave_write object representing the open WAV file.
        """
        # Close the WAV file.
        wav_file.close()
        # Log the successful export of the WAV file.
        log.info("--> Wav file correctly exported")

    @staticmethod
    def __write_unico_file(dataframe, file_path, out_format, mode):
        """
        Writes a DataFrame to a file in a specified ST Unico format (txt, csv, tsv).

        :param dataframe: The DataFrame to write.
        :param file_path: The base path for the output file.
        :param out_format: The output format ('txt', 'csv', 'tsv').
        :param mode: The file writing mode ('w' for write, 'a' for append).
        """
        # Choose the appropriate method to write the DataFrame based on the output format.
        if out_format.lower() == "txt":
            HSDatalogConverter.to_xsv(dataframe, file_path, ".txt", "\t", mode)
        elif out_format.lower() == "csv":
            HSDatalogConverter.to_csv(dataframe, file_path, mode)
        else:
            HSDatalogConverter.to_tsv(dataframe, file_path, mode)

    @staticmethod
    def to_unico(
        file_path, df, out_format="txt", mode="w", with_times=False, columns_labels="default"
    ):
        """
        Writes a DataFrame to a file in a specified ST Unico format with an optional time column.

        :param file_path: The base path for the output file.
        :param df: The DataFrame to write.
        :param out_format: The output format ('txt', 'csv', 'tsv').
        :param mode: The file writing mode ('w' for write, 'a' for append).
        :param with_times: A boolean indicating whether to include the 'Time' column.
        :param columns_labels: [Optional] Column labels for the output file. Default is "default".
        :return: True if the operation is successful.
        """
        if not with_times:
            if columns_labels == "mlc_tool":
                df = df.drop("Time[s]", axis=1)
            else:
                df = df.drop("Time", axis=1)
        HSDatalogConverter.__write_unico_file(df, file_path, out_format, mode)
        return True

    @staticmethod
    def rename_dataframe_columns(comp_name, dataframe, columns_labels, tags_columns_names=[]):
        """
        Renames DataFrame columns based on component names and labeling schemes.
        :param comp_name: The component name to use in renaming.
        :param dataframe: The DataFrame whose columns need to be renamed.
        :param columns_labels: A labeling scheme for the columns (e.g., 'mlc_tool').
        :param tags_columns_names: A list of column names to exclude from renaming.
        :return: The DataFrame with renamed columns.
        """
        # Define a list of columns to exclude
        cols_to_exclude = ["Time", "Time[s]"]
        if len(tags_columns_names) > 0:
            cols_to_exclude.extend(tags_columns_names)
        # Apply a specific labeling scheme to the columns based
        # on the provided columns_labels argument.
        if columns_labels == "mlc_tool":
            # Define a prefix to add to column names based on the component name.
            prefix = f"{comp_name}"
            new_names = {}
            for col in dataframe.columns:
                if col not in cols_to_exclude:
                    sensor_type_str = col.split(" ")[0]
                    # Create a dictionary of column name mappings with the new prefix.
                    if "_" in sensor_type_str:
                        new_col_name = (
                            prefix
                            + "_"
                            + sensor_type_str.split("_")[-1]
                            + " "
                            + dataframe.columns[1].split(" ")[-1]
                        )
                    else:
                        new_col_name = prefix + " " + dataframe.columns[1].split(" ")[-1]
                    new_names[col] = new_col_name
        else:
            # Define a suffix to add to column names based on the component name.
            suffix = f"_{comp_name}"
            # Create a dictionary of column name mappings with the new suffix.
            new_names = {
                col: col + suffix for col in dataframe.columns if col not in cols_to_exclude
            }
        # Rename columns in the dataframe according to the new names mapping.
        dataframe = dataframe.rename(columns=new_names)
        return dataframe

    @staticmethod
    def merge_dataframes(dataframes, comp_names, columns_labels=None, tags_columns_names=[]):
        """
        Merges multiple DataFrames based on their 'Time' columns and renames the columns.

        :param dataframes: A list of DataFrames to merge.
        :param comp_names: A list of component names corresponding to each DataFrame.
        :param columns_labels: A labeling scheme for the columns (e.g., 'mlc_tool').
        :param tags_columns_names: A list of column names to exclude from renaming.
        :return: The merged DataFrame.
        """
        # Define a list of columns to exclude
        cols_to_exclude = ["Time"]
        if len(tags_columns_names) > 0:
            cols_to_exclude.extend(tags_columns_names)
        # Rename columns and merge DataFrames.
        for i, cn in enumerate(comp_names):
            if columns_labels is not None:
                # Apply a specific labeling scheme to the columns based on the provided
                # columns_labels argument.
                if columns_labels == "mlc_tool":
                    # Define a prefix to add to column names based on the component name.
                    prefix = f"{cn}"
                    new_names = {}
                    for col in dataframes[i].columns:
                        if col not in cols_to_exclude:
                            sensor_type_str = col.split(" ")[0]
                            # Create a dictionary of column name mappings with the new prefix.
                            if "_" in sensor_type_str:
                                new_col_name = (
                                    prefix
                                    + "_"
                                    + sensor_type_str.split("_")[-1]
                                    + " "
                                    + dataframes[i].columns[1].split(" ")[-1]
                                )
                            else:
                                new_col_name = (
                                    prefix + " " + dataframes[i].columns[1].split(" ")[-1]
                                )
                            new_names[col] = new_col_name
                else:
                    # Define a suffix to add to column names based on the component name.
                    suffix = f"_{cn}"
                    # Create a dictionary of column name mappings with the new suffix.
                    new_names = {
                        col: col + suffix
                        for col in dataframes[i].columns
                        if col not in cols_to_exclude
                    }
                # Rename columns in the dataframe according to the new names mapping.
                dataframes[i] = dataframes[i].rename(columns=new_names)

        # Convert non-numeric 'Time' column to numeric for all dataframes to ensure proper merging.
        for df in dataframes:
            df["Time"] = df["Time"].astype(float)

        # Merge dataframes using an 'asof' merge, which is a sort of fuzzy merge suitable for
        # time series data. It merges on the nearest key rather than requiring an exact match.
        merged_df = dataframes[0]
        for i in range(1, len(dataframes)):
            merged_df = pd.merge_asof(merged_df, dataframes[i], on="Time", direction="nearest")

        # Return the merged DataFrame.
        return merged_df

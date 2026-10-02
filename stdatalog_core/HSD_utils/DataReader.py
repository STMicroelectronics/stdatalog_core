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
Incremental, zero-copy oriented parser for streaming binary data coming from
sensors, algorithms, or actuators (e.g., MC telemetries). It buffers raw bytes,
reconstructs complete samples (and optional timestamps), converts them to NumPy
arrays (including 24-bit to 32-bit expansion), applies sensitivity scaling, and
emits parsed blocks via a user-provided callback.
Main components
- DataReader: Stateful parser that:
    - Handles interleaved and non‑interleaved layouts.
    - Supports samples-per-timestamp (spts) framing with 8-byte double timestamps.
    - Accepts heterogeneous element sizes (1/2/3/4/8 bytes).
    - Converts 24-bit integers using TypeConversion utilities.
    - Outputs data as DataClass(comp_name, dict[channel_index -> np.ndarray]).
    - Manages residual bytes between calls (partial samples/timestamps).
Key behaviors
- With samples_per_ts > 0, the stream is framed as:
    [data (spts * dimensions * sample_size)] + [timestamp (8 bytes, double)]
    Partial frames or split timestamps are carried over across feed_data calls.
- When sample_size == 3, values are expanded to 32-bit before unpacking.
- Interleaved data can be returned split per-axis or as a single flattened array.
Public API (summary)
- DataReader.__init__(output_function, comp_name, samples_per_ts, dimensions,
    sample_size, data_format, sensitivity=1, interleaved_data=True, flat_raw_data=False)
- DataReader.feed_data(data): Incrementally parse incoming bytes and emit DataClass.
- Internal helpers manage sample alignment, timestamp cadence, and buffering.
Expected input/output
- Input: objects with a .data attribute containing raw bytes (bytearray/bytes).
- Output: DataClass instances passed to output_function, carrying:
    - name: comp_name
    - data: dict[channel_index -> np.ndarray] scaled by sensitivity
Example
-------
        def sink(dc: DataClass):
                # Consume parsed data here
                pass
        reader = DataReader(
                output_function=sink,
                comp_name="accel",
                samples_per_ts=32,
                dimensions=3,
                sample_size=2,
                data_format=np.int16,
                sensitivity=0.061,          # example scale (e.g., mg/LSB)
                interleaved_data=True,
                flat_raw_data=False
        )
        # Simulated packet with .data bytes attribute
        class Packet: pass
        pkt = Packet()
        pkt.data = b"\x00\x01" * (32 * 3) + struct.pack("=d", 0.0)
        reader.feed_data(pkt)
Notes
- The module relies on NumPy for efficient buffer views and on struct for packed I/O.
- TypeConversion.int24_buffer_to_int32_buffer is used for 24-bit expansion.
- The parsing logic is robust to arbitrarily chunked input (e.g., from sockets/USB).
"""

import math
import struct
import numpy as np

from stdatalog_core.HSD.utils.type_conversion import TypeConversion

from .DataClass import DataClass


class DataReader(object):
    """
    Incremental parser for streaming data from sensors, algorithms, or actuators (MC telemetries).

    Responsibility:
    - Buffer raw bytes, extract complete samples (and optional timestamps).
    - Support interleaved/non‑interleaved layouts and samples‑per‑timestamp (spts).
    - Convert 24‑bit integers, unpack to NumPy arrays, apply sensitivity scaling.
    - Emit parsed data via the provided output_function as DataClass objects.

    Attributes:
        output_function: Callable accepting DataClass(comp_name, dict[int -> np.ndarray]).
        comp_name: Component name associated with the stream.
        samples_per_ts: Samples per timestamp (0 means no timestamp in the stream).
        dimensions: Number of axes/channels per sample.
        sample_size: Bytes per element (1/2/3/4/8).
        data_format: Struct/NumPy dtype code (e.g., 'f', 'i2').
        sensitivity: Multiplier applied to output samples.
        interleaved_data: True if data is [x0,y0,z0,x1,y1,z1,...], else grouped per sample.
        flat_raw_data: True to output a single flattened channel (no per‑axis split).
        data_size: Bytes of one data block (per frame) excluding timestamp.
        time_size: Bytes of timestamp (0 or 8).
        time_format: Struct code for timestamp ('d' or '').
        next_is_timestamp: Whether the next bytes after data are a timestamp.
        data_buffer: NumPy array buffer with parsed numeric samples.
        data_dict: Dict channel_index -> np.ndarray ready for output.
        data_samples_counter: Count of parsed data elements towards spts*dimensions.
        rem_dim_bytes: Residual bytes from incomplete samples at buffer edges.
        missing_time_bytes: Residual timestamp bytes awaited in next packet.

    Key methods:
        - feed_data(data): Main entrypoint; parse incoming bytes and emit DataClass.
        - fill_data_buffer(data): Unpack bytes into numeric buffer and manage residues.
        - calculate_data_to_extract(data): Trim to whole samples respecting dimensions.
        - update_data_samples_counter_and_time_flag(): Maintain spts/timestamp cadence.

    Notes:
        - When sample_size == 3, 24‑bit values are expanded to 32‑bit before unpack.
        - With spts > 0, each full frame is data (spts*dim) followed by an 8‑byte double
            timestamp; partial frames/timestamps are handled across calls.
        - Output per channel is split only if interleaved_data and not flat_raw_data.
    """

    def __init__(
        self,
        output_function,
        comp_name,
        samples_per_ts,
        dimensions,
        sample_size,
        data_format,
        sensitivity=1,
        interleaved_data=True,
        flat_raw_data=False,
    ):
        """
        Initialize the DataReader. The DataReader will parse incoming raw byte data
        according to the specified parameters and output structured data via the output_function.
        :param output_function: Callable to receive parsed DataClass objects.
        :param comp_name: Component name for the data stream.
        :param samples_per_ts: Number of samples per timestamp (0 if no timestamps).
        :param dimensions: Number of axes/channels per sample.
        :param sample_size: Size in bytes of each sample element (1,2,3,4,8).
        :param data_format: Struct/NumPy format code for unpacking samples.
        :param sensitivity: Scaling factor applied to output samples.
        :param interleaved_data: True if data is interleaved per sample.
        :param flat_raw_data: True to output a single flattened channel.
        """
        self.output_function = output_function
        self.comp_name = comp_name
        self.samples_per_ts = samples_per_ts
        self.dimensions = dimensions
        self.sample_size = sample_size
        self.sensitivity = sensitivity
        self.interleaved_data = interleaved_data
        self.flat_raw_data = flat_raw_data
        if samples_per_ts != 0:
            self.data_size = self.sample_size * self.samples_per_ts * self.dimensions
            self.time_size = 8
            self.time_format = 'd'
        else:
            self.data_size = self.sample_size * self.dimensions
            self.time_size = 0
            self.time_format = ''
        self.data_format = data_format
        self.next_is_timestamp = False

        # Precomputed constants for the zero-copy multi-packet parsing fast path.
        # packet_size is the byte length of one full frame (data + optional timestamp).
        self.packet_size = self.data_size + self.time_size
        # When timestamps are present, frames are parsed with a NumPy structured
        # dtype so the data field can be extracted while the trailing timestamp
        # bytes are skipped, without any per-element Python loops.
        if samples_per_ts != 0:
            self._n_frame_vals = self.samples_per_ts * self.dimensions
            self._frame_dtype = np.dtype(
                [
                    ('data', self.data_format, (self._n_frame_vals,)),
                    ('ts', 'V{}'.format(self.time_size)),
                ]
            )
        else:
            self._n_frame_vals = self.dimensions
            self._frame_dtype = None

        self.data_buffer = []
        self.data_dict = {}

        self.data_samples_counter = 0
        self.rem_dim_bytes = ''
        self.missing_time_bytes = 0

        self.rem_data_bytes = ''

    def get_largest_mult_of_n_lower_than_m(self, n, m):
        """
        Get the largest multiple of n that is lower than m.
        :param n: The number to find multiples of.
        :param m: The upper limit.
        :return: The largest multiple of n lower than m.
        """
        return int((m // n) * n)  # Largest multiple of n lower than m

    def calculate_data_to_extract(self, data):
        """
        Calculate the amount of data to extract as complete samples respecting dimensions.
        :param data: Raw byte data.
        :return: Tuple of (int_rem_data_bytes, data_samples).
        """
        # if data lenght is multiple of dimensions
        int_rem_data_bytes = len(data)
        data_samples = int_rem_data_bytes / self.sample_size
        # else, it is needed to select the largest multiple of dimensions
        # lower than the length of data_samples
        if data_samples % self.dimensions != 0:
            data_samples = self.get_largest_mult_of_n_lower_than_m(self.dimensions, data_samples)
            int_rem_data_bytes = self.sample_size * data_samples
        return [int_rem_data_bytes, data_samples]

    def update_data_samples_counter_and_time_flag(self):
        """
        Update the data samples counter and set the flag indicating if the next data is a timestamp.
        """
        if self.data_samples_counter == self.samples_per_ts * self.dimensions:
            self.data_samples_counter = 0
            self.next_is_timestamp = True
        else:
            self.next_is_timestamp = False

    def expand_int24_payload_frames(self, data, n_cplt_packet):
        """Expand only frame data from int24 to int32, preserving raw timestamp bytes."""
        if self.samples_per_ts == 0 or self.time_size == 0:
            return TypeConversion.int24_buffer_to_int32_buffer(
                data[: n_cplt_packet * (self.data_size + self.time_size)]
            )

        packet_size = self.data_size + self.time_size
        expanded_frames = bytearray()
        for packet_idx in range(n_cplt_packet):
            packet_start = packet_idx * packet_size
            data_end = packet_start + self.data_size
            packet_end = packet_start + packet_size
            expanded_frames.extend(TypeConversion.int24_buffer_to_int32_buffer(data[packet_start:data_end]))
            expanded_frames.extend(data[data_end:packet_end])

        return bytes(expanded_frames)

    def fill_data_buffer(self, data):
        """
        Fill the data buffer by unpacking raw byte data into numeric samples.
        This method handles complete and partial packets, manages residual bytes,
        and updates the internal state accordingly. It supports both interleaved
        and non-interleaved data formats, as well as samples-per-timestamp configurations.
        :param data: Raw byte data.
        :return: None
        """
        # extract the number of complete packet (data+timestamp)
        # data length is less than the full packet data size:
        # --> (sample_size * samples_size_per_ts * dim)
        if len(data) <= self.data_size:
            # Calculate the right amount of data to extract
            # (data already includes any previously buffered residual bytes)
            [int_rem_data_bytes, data_samples] = self.calculate_data_to_extract(data)
            # update the data_samples_counter
            self.data_samples_counter = int(data_samples)
            # copy data into buffer
            if self.sample_size == 3:
                a = TypeConversion.int24_buffer_to_int32_buffer(data[:int_rem_data_bytes])
                self.data_buffer = np.frombuffer(a, dtype=self.data_format, count=int(data_samples))
            else:
                self.data_buffer = np.frombuffer(
                    data[:int_rem_data_bytes], dtype=self.data_format, count=int(data_samples)
                )
            # Take remaining bytes, if any (e.g., data ends with incomplete sample (x,y,), no z)
            self.rem_dim_bytes = data[int_rem_data_bytes:]
            # update data_samples_counter (remain the same) and timestamp flag (to False)
            self.update_data_samples_counter_and_time_flag()

        elif len(data) > self.data_size and len(data) < (self.data_size + self.time_size):
            # Calculate residual bytes to complete the next timestamp
            self.missing_time_bytes = (self.data_size + self.time_size) - len(data)
            # Trim data to avoid interpreting partial timestamp as data
            data = data[: self.data_size]
            # Recursively call with trimmed data
            self.fill_data_buffer(data)

        else:
            # n_cplt_packet >= 1 here; 0 case handled in the branch above
            packet_size = self.packet_size
            n_cplt_packet = len(data) // packet_size
            # Zero-copy extraction of the complete frames.
            # Each frame is [data (n_frame_vals samples)] + [timestamp (time_size bytes)].
            # A NumPy structured dtype lets us reinterpret the raw bytes and pick
            # only the "data" field, dropping timestamps without any per-element
            # Python unpacking / list building / strided deletion.
            if self.sample_size == 3:
                # 24-bit samples are expanded to 32-bit first (timestamps kept raw).
                data_t = self.expand_int24_payload_frames(data, n_cplt_packet)
            else:
                data_t = data[: n_cplt_packet * packet_size]
            if self.samples_per_ts != 0:
                frames = np.frombuffer(data_t, dtype=self._frame_dtype, count=n_cplt_packet)
                self.data_buffer = np.ascontiguousarray(frames['data']).reshape(-1)
            else:
                self.data_buffer = np.frombuffer(data_t, dtype=self.data_format)

            # take the remaining bytes (incomplete frame after the last full packet)
            self.rem_data_bytes = data[n_cplt_packet * packet_size :]

            # If rem_data_bytes includes a partial timestamp, handle it
            if len(self.rem_data_bytes) > self.data_size:
                # Calculate residual bytes to complete the timestamp
                self.missing_time_bytes = (self.data_size + self.time_size) - len(self.rem_data_bytes)
                # Trim rem_data_bytes to avoid interpreting timestamp bytes as data
                self.rem_data_bytes = self.rem_data_bytes[: self.data_size]
                data = data[: n_cplt_packet * packet_size + self.data_size]

            # calculate the right amount of data to extract
            [int_rem_data_bytes, data_samples] = self.calculate_data_to_extract(self.rem_data_bytes)
            # update the data_samples_counter
            self.data_samples_counter = int(data_samples)
            # add remaining extracted data into the data_buffer (zero-copy view + concat)
            if self.data_samples_counter > 0:
                rem_start = n_cplt_packet * packet_size
                if self.sample_size == 3:
                    a = TypeConversion.int24_buffer_to_int32_buffer(data[rem_start : rem_start + int_rem_data_bytes])
                    remainder = np.frombuffer(a, dtype=self.data_format, count=self.data_samples_counter)
                else:
                    remainder = np.frombuffer(
                        data[rem_start : rem_start + int_rem_data_bytes],
                        dtype=self.data_format,
                        count=self.data_samples_counter,
                    )
                self.data_buffer = np.concatenate((self.data_buffer, remainder))
            # Take remaining bytes if data ends with an incomplete sample (x,y,), missing z)
            self.rem_dim_bytes = data[n_cplt_packet * packet_size + int_rem_data_bytes :]
            # Update data_samples_counter and timestamp flag
            self.update_data_samples_counter_and_time_flag()

    def feed_data(self, data):
        """
        Feed raw byte data into the DataReader for parsing.
        This method handles buffering, parsing, and emitting structured data
        according to the configured parameters.
        :param data: Raw byte data to parse.
        :return: None
        """
        if self.comp_name == self.comp_name:
            if len(self.rem_dim_bytes) != 0:
                # append residual data bytes
                data.data = self.rem_dim_bytes + data.data
                # print(array.array('h', data.data))
            if self.missing_time_bytes != 0:
                # drop residual timestamp bytes
                data.data = data.data[self.missing_time_bytes :]
                self.missing_time_bytes = 0
                self.next_is_timestamp = False
            if self.data_samples_counter == 0:
                if not self.next_is_timestamp:
                    # received data starts with data
                    self.fill_data_buffer(data.data)
                else:
                    # received data starts with timestamp
                    data.data = data.data[len(self.rem_dim_bytes) + self.time_size :]  # removing the timestamp
                    if len(self.rem_dim_bytes) != 0:
                        # self.data_samples_counter += len(self.rem_dim_bytes)/self.sample_size
                        self.fill_data_buffer(self.rem_dim_bytes + data.data)  # place residual bytes in head if any
                    else:
                        self.fill_data_buffer(data.data)
            else:
                # Determine missing samples to complete a full packet (spts * dimensions)
                # data_samples_counter counts data. If multiple dim, data one dimension is one data
                diff = (
                    self.samples_per_ts * self.dimensions
                ) - self.data_samples_counter  # number of samples to complete a full packet
                if len(data.data) < (diff * self.sample_size):
                    # Raw bytes: multiply samples by sample_size to get byte count
                    # calculate the right amount of data to extract
                    [int_rem_data_bytes, data_samples] = self.calculate_data_to_extract(data.data)
                    # copy data into buffer
                    if self.sample_size == 3:
                        a = TypeConversion.int24_buffer_to_int32_buffer(data.data[:int_rem_data_bytes])
                        self.data_buffer = np.frombuffer(
                            a,
                            dtype=self.data_format,
                            count=int(int_rem_data_bytes / self.sample_size),
                        )
                    else:
                        self.data_buffer = np.frombuffer(
                            data.data[:int_rem_data_bytes],
                            dtype=self.data_format,
                            count=int(int_rem_data_bytes / self.sample_size),
                        )
                    # update the data_samples_counter
                    self.data_samples_counter += int(data_samples)
                    # Take remaining bytes, if any (e.g., data ends with incomplete sample)
                    self.rem_dim_bytes = data.data[int_rem_data_bytes:]
                else:
                    # copy data into buffer
                    if self.sample_size == 3:
                        a = TypeConversion.int24_buffer_to_int32_buffer(data.data[: diff * self.sample_size])
                        self.data_buffer = np.frombuffer(a, dtype=self.data_format, count=diff)
                    else:
                        self.data_buffer = np.frombuffer(
                            data.data[: diff * self.sample_size], dtype=self.data_format, count=diff
                        )
                    # reinit data_samples_counter
                    self.data_samples_counter = 0
                    if len(self.data_buffer) == (len(data.data) / self.sample_size):
                        # next received packet will start with a timestamp
                        self.next_is_timestamp = True
                        self.rem_dim_bytes = ''
                    else:
                        # Remove the timestamp (excluding the first)
                        if (len(self.data_buffer) * self.sample_size) + self.time_size > len(data.data):
                            # Remove partial timestamp,
                            # Remainig bytes will arrive in the next packet
                            self.missing_time_bytes = (
                                (len(self.data_buffer) * self.sample_size) + self.time_size
                            ) - len(data.data)
                            # print("Sensor Name: {}".format(self.comp_name))
                            # print(f"timestamp splitted->missing_time: {self.missing_time_bytes}")
                            data.data = data.data[
                                (len(self.data_buffer) * self.sample_size)
                                + (self.time_size - self.missing_time_bytes) :
                            ]
                        else:
                            data.data = data.data[(len(self.data_buffer) * self.sample_size) + self.time_size :]

                        # fill data buffer
                        self.fill_data_buffer(data.data)

            if len(self.data_buffer) > 0:
                if not self.interleaved_data:
                    # if self.samples_per_ts == 0:
                    for i in range(int(len(self.data_buffer) / self.dimensions)):
                        self.data_dict[i] = (
                            np.array(
                                self.data_buffer[i * self.dimensions : (i * self.dimensions) + self.dimensions],
                                dtype=self.data_format if self.flat_raw_data else 'f',
                            )
                            * self.sensitivity
                        )
                else:
                    if self.flat_raw_data:
                        self.data_dict[0] = self.data_buffer * self.sensitivity
                    else:
                        for i in range(self.dimensions):
                            self.data_dict[i] = (
                                np.array(self.data_buffer[i :: self.dimensions], dtype='f') * self.sensitivity
                            )

                # Add data to a plot
                self.output_function(DataClass(self.comp_name, self.data_dict))

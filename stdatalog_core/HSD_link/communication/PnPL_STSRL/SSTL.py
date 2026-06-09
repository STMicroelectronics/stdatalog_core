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
"""
SSTL (Simple Serial Transport Layer) over ASPEP

Overview:
    - Implements segmentation and reassembly for command/response frames above ASPEP.
    - Packs/unpacks the 4-byte SSTL header and coordinates first/middle/last segments.

Packet format (little endian):
    - Header size: 4 bytes. Bit fields (LSB first):
        vv:2, rrr:3, cr:3, fin:1, ch_num:7, sequence_num:16.
        For the first segment of a multi-part packet, bits 9–31 encode total_packet_size (23 bits)
        and fin=0. Middle segments have fin=0 and an increasing sequence_num. The last segment has
        fin=1 with its sequence_num.

Main classes:
    - SSTLHeader: parsed header fields container.
    - SSTLPacket: header + payload bytes.
    - SSTL: manager using an `ASPEP` instance for low-level I/O.

Key methods:
    - send_command(ser, message, semaphore): split and send a buffer with proper segment headers.
    - send_ack(ser, sequence_num): send an ACK frame with no payload.
    - receive(ser): parse one ASPEP frame and return SSTLPacket; tracks segmentation state.
    - send_bytes(ser, byte_array): debug helper to send a raw byte array.

Usage example:
    - sstl = SSTL(serial_port)
    - sstl.send_command(serial_port, b"payload", semaphore)
    - pkt = sstl.receive(serial_port)
    - if pkt and pkt.header.fin:
            # handle last segment
            pass

Notes:
    - `MAX_RX_SLAVE_PKT_SIZE` is derived from ASPEP limits minus SSTL header size.
    - All header bit operations and packing use little endian.
"""

from enum import Enum

from stdatalog_core.HSD_link.communication.PnPL_STSRL.ASPEP import (
    ASPEP,
    ASPEPType,
    ASPEPError
)

class SSTLError(Exception):
    """Exception raised for SSTL protocol errors."""

class SSTLProtocolError(SSTLError):
    """Error raised when the underlying ASPEP transport fails."""
    def __init__(self, message, original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception

class DataPayloadType(Enum):
    """Payload type used by the connected device."""
    # High Speed Datalog payload.
    HSD = 0
    # CUSTOM payload.
    CUSTOM = 1

class SSTLCommandRequestType(Enum):
    """Available Command / Request (cr) values."""
    # Binary data packet, HSD payload.
    BINARY_HSD_PAYLOAD = 0b000
    # Command / Request packet, not applicable for receiving packets (only from host to board).
    COMMAND_REQUEST = 0b001
    # Response packet, may require segmentation handling.
    RESPONSE = 0b010
    # Asynchronous property change / notification.
    ASYNC_NOTIFICATION = 0b011
    # Binary data packet, CUSTOM payload.
    BINARY_CUSTOM_PAYLOAD = 0b100
    # Textual data packet.
    TEXTUAL_DATA = 0b111

# Size of the counter for HSD payload in bytes.
HSD_PAYLOAD_COUNTER_SIZE_B = 4
class SSTLHeader:
    """
    SSTL Packet Header.
    Attributes:
        vv (int): Version value (2 bits).
        rrr (int): Reserved bits (3 bits).
        cr (int): Command/Response type (3 bits).
        fin (int): Final segment flag (1 bit).
        ch_num (int): Channel number (7 bits).
        sequence_num (int): Sequence number (16 bits).
        total_packet_size (int): Total packet size for segmented packets (23 bits).
    """

    def __init__(self, vv, rrr, cr, fin, ch_num, sequence_num, total_packet_size):
        self.vv = vv
        self.rrr = rrr
        self.cr = cr
        self.fin = fin
        self.ch_num = ch_num
        self.sequence_num = sequence_num
        self.total_packet_size = total_packet_size


class SSTLPacket:
    """
    SSTL Packet containing header and data.
    Attributes:
        header (SSTLHeader): SSTL packet header.
        data (bytes): SSTL packet data.
    """

    def __init__(self, header, data=None):
        self.header: SSTLHeader = header
        self.data = data


class SSTL:
    """
    SSTL (Simple Segmented Transport Layer) manager for handling SSTL communication over ASPEP.
    This class provides methods to send and receive SSTL packets,
    including support for segmentation.

    Attributes:
        aspep_manager (ASPEP): ASPEP manager for low-level communication.
        MAX_RX_SLAVE_PKT_SIZE (int): Maximum RX slave packet size based on ASPEP parameters.
        tx_segmentation (bool): Flag indicating if TX segmentation is active.
        rx_segmentation (bool): Flag indicating if RX segmentation is active.
    """

    # SSTL Protocol Constants
    HEADER_SIZE = 4
    PROTOCOL_VERSION = 0
    PROTOCOL_RRR = 0
    COMMAND_REQUEST_TYPE = 1
    RESPONSE_TYPE = 2

    def __init__(self, ser) -> None:
        """
        Initialize the SSTL manager with the given serial port.
        Also initializes the ASPEP manager.
        Sets the maximum RX slave packet size based on ASPEP parameters.
        Initializes segmentation flags.
        Note: The serial port object is passed to the ASPEP manager that handles
        the low-level communication.

        :param ser: Serial port object
        """
        self.aspep_manager = ASPEP(ser)
        self.MAX_RX_SLAVE_PKT_SIZE = (
            self.aspep_manager.RXS_SLAVE_MAX - self.aspep_manager.HEADER_SIZE
        )
        self.tx_segmentation = False
        self.rx_segmentation = False

    def __build_command_header(self, channel_id=0, sequence_num=0):
        """
        Build the header for an SSTL command packet.
        The fin flag is set to 1.
        Note: This method is used for single packets and the last segment of a segmented packet.

        :param channel_id: Channel ID
        :param sequence_num: Sequence number
        :return: Header as an integer
        """
        header = (
            SSTL.PROTOCOL_VERSION
            | (SSTL.PROTOCOL_RRR << 2)
            | (SSTL.COMMAND_REQUEST_TYPE << 5)
            | (1 << 8)
            | (channel_id << 9)
            | (sequence_num << 16)
        )
        return header

    def __build_middle_segment_header(self, channel_id=0, sequence_num=0):
        """
        Build the header for a middle segment of a segmented SSTL command packet.
        The fin flag is set to 0.
        Note: This method is used for all segments except the first and last.

        :param channel_id: Channel ID
        :param sequence_num: Sequence number
        :return: Header as an integer
        """
        header = (
            SSTL.PROTOCOL_VERSION
            | (SSTL.PROTOCOL_RRR << 2)
            | (SSTL.COMMAND_REQUEST_TYPE << 5)
            | (0 << 8)  # FIN=0
            | (channel_id << 9)
            | (sequence_num << 16)
        )
        return header

    def __build_segmented_command_header(self, channel_id=0, sequence_num=0, total_packet_size=0):
        """
        Build the header for the first segment of a segmented SSTL command packet.
        The fin flag is set to 0, and the total_packet_size is included in bits
        9-31 of the header.
        Note: This method is used only for the first segment of a segmented packet.

        :param channel_id: Channel ID (Not used; always 0 for first segment)
        :param sequence_num: Sequence number (Not used; always 0 for first segment)
        :param total_packet_size: Total size of the complete packet
        :return: Header as an integer
        """
        _ = channel_id  # Not used in this implementation (always 0 for first segment)
        _ = sequence_num  # Not used in this implementation (always 0 for first segment)

        header = (
            SSTL.PROTOCOL_VERSION
            | (SSTL.PROTOCOL_RRR << 2)
            | (SSTL.COMMAND_REQUEST_TYPE << 5)
            | (0 << 8)  # fin = 0
            | ((total_packet_size & 0x7FFFFF) << 9)
        )  # total_packet_size in bits 9-31
        return header

    def send_command(self, ser, message, semaphore):
        """
        Send a message using SSTL, segmenting if necessary.
        - First segment: fin=0, channel=0, sequence=0, total_packet_size set
        - Middle segments: fin=0, channel=0, sequence incremented
        - Last segment: fin=1, channel=0, sequence incremented
        The semaphore is used to synchronize sending of segments. The caller should
        release the semaphore after receiving an acknowledgment for each segment
        except the last one.
        Note: This method assumes that the caller manages the semaphore for segmented packets.

        :param ser: Serial port object
        :param message: Message to send (str or bytes)
        :param semaphore: Semaphore
        :return: None
        """
        byte_array = message.encode('utf-8') if isinstance(message, str) else message

        # Maximum payload size per SSTL packet (4 bytes for ASPEP header)
        max_payload = self.aspep_manager.RXS_SLAVE_MAX - SSTL.HEADER_SIZE - 4
        total_size = len(byte_array)
        if total_size <= max_payload:
            self.rx_segmentation = False
            # Single packet, fin=1, sequence=0
            header = self.__build_command_header(channel_id=0, sequence_num=0)
            header = header.to_bytes(SSTL.HEADER_SIZE, "little")
            packet = header + byte_array
            self.aspep_manager.send_data(ser, packet)
            return
        # Segmented transmission
        self.rx_segmentation = True
        num_segments = (total_size + max_payload - 1) // max_payload
        for i in range(num_segments):
            start = i * max_payload
            end = min(start + max_payload, total_size)
            chunk = byte_array[start:end]
            if i == 0:
                # First segment: fin=0, total_packet_size set
                header = self.__build_segmented_command_header(
                    channel_id=0, sequence_num=0, total_packet_size=total_size
                )
            elif i == num_segments - 1:
                # Last segment: fin=1, sequence_num=i
                header = self.__build_command_header(channel_id=0, sequence_num=i)
            else:
                # Middle segments: fin=0, sequence_num=i
                header = self.__build_middle_segment_header(channel_id=0, sequence_num=i)
            header_bytes = header.to_bytes(SSTL.HEADER_SIZE, "little")
            packet = header_bytes + chunk
            self.aspep_manager.send_data(ser, packet)
            if i < num_segments - 1:
                semaphore.acquire()
        return

    def send_ack(self, ser, sequence_num):
        """
        Send an SSTL acknowledgment packet.
        The acknowledgment packet has no payload and uses the provided sequence number.
        Note: This method does not handle segmentation as ACK packets are always single packets.

        :param ser: Serial port object
        :param sequence_num: Sequence number to acknowledge
        :return: None
        """
        header = self.__build_command_header(0, sequence_num)
        header = header.to_bytes(SSTL.HEADER_SIZE, "little")
        print("\n\rFrame: ", header)
        self.aspep_manager.send_data(ser, header)

    # debug method
    def send_bytes(self, ser, byte_array):
        """
        Send raw bytes as an SSTL packet.
        This method is primarily for debugging purposes and sends the provided byte array directly.
        :param ser: Serial port object
        :param byte_array: Byte array to send

        :return: None
        """
        self.aspep_manager.send_data(ser, byte_array)

    def receive(self, ser):
        """
        Receive a SSTL packet and parse it.
        Returns an SSTLPacket object or None if no valid packet is received.
        This method handles segmentation based on the fin flag.
        Note: This method assumes that the caller manages the semaphore for segmented packets.

        :param ser: Serial port object
        :return: SSTLPacket or None
        """
        try:
            response = self.aspep_manager.receive_bytes(ser)
            if response is None or response.data is None or len(response.data) == 0:
                raise ValueError("No valid data received in ASPEP response.")
            
            if response.header.p_type in (ASPEPType.Data, ASPEPType.Async, ASPEPType.Response):
                if len(response.data) < SSTL.HEADER_SIZE:
                    raise ValueError(f"SSTL packet too short: {len(response.data)} bytes")
                # extract the vv value from the response header
                vv = response.data[0] & 0b11
                # extract the rrr value from the response header
                rrr = (response.data[0] >> 2) & 0b111
                # extract the cr value from the response header
                cr = response.data[0] >> 5
                # extract the fin value from the response header
                fin = response.data[1] & 0b1
                if fin == 0 and self.tx_segmentation == False:
                    # extract the total_packet_size value from the response header
                    total_packet_size = (
                        ((response.data[1] >> 1) & 0b1111111)
                        | (response.data[2] << 7)
                        | (response.data[3] << 15)
                    )
                    self.tx_segmentation = True
                    ch_num = 0
                    sequence_num = 0
                else:
                    # extract the ch_num value from the response header
                    ch_num = response.data[1] >> 1
                    # extract sequence value by combining response[3] and response[2]
                    # into a single value
                    sequence_num = (response.data[3] << 8) | response.data[2]
                    total_packet_size = 0
                    if fin == 1:
                        self.tx_segmentation = False
                sstl_header = SSTLHeader(vv, rrr, cr, fin, ch_num, sequence_num, total_packet_size)
                data = response.data[SSTL.HEADER_SIZE :]
                if len(data) == 0:
                    print("SSTL packect received with empty data")
                if cr != SSTLCommandRequestType.BINARY_HSD_PAYLOAD.value and \
                    cr != SSTLCommandRequestType.BINARY_CUSTOM_PAYLOAD.value and \
                    len(data) > 0 and data[-1] == 0:
                    # remove the trailing null character if present for command/response packets
                    data = data[:-1]
                sstl_packet = SSTLPacket(sstl_header, data)
                return sstl_packet
            else:
                raise ValueError(f"Unexpected ASPEP packet type: {response.header.p_type}")
        except ASPEPError as e:
            # Wrap ASPEP layer errors into a specific low-level transport error
            raise SSTLProtocolError("Low-level ASPEP transport error") from e

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
ASPEP (ASSP Packet Link Protocol)

Overview:
    - Low-level transport for framed exchanges over serial between host and device.
    - Encodes a 4-byte header with packet type, parity, length or parameters, and CRC.

Header layout (little endian, 32 bits):
    - Bits 0..2: type (Data, Async, Response, Beacon, Ping, Error)
    - Bit 3: parity
    - Bits 4..16: payload_length (for Data/Async/Response) or fields per type
    - Bits 28..31: CRC4 of lower 28 bits (placed in the top 4 bits)

Main entities:
    - ASPEPHeader/ASPEPPacket: parsed header and optional payload.
    - Inner headers for Beacon/Ping/Data/Async/Response/Error.
    - ASPEP: manager that builds/sends headers and reads/parses frames from a serial port.

Key methods:
    - send_data(ser, bytes): build and send a data frame if size <= RXS_SLAVE_MAX.
    - send_beacon(ser): advertise RX/TX buffer sizes (host to device).
    - send_ping(ser): temporary basic ping, to be replaced by a proper mechanism.
    - receive_bytes(ser): blocking read and parse of one ASPEP frame.

Notes:
    - All `ser` interactions are blocking; caller configures timeouts appropriately.
    - CRC4 is computed over the lower 28 bits; result is placed in bits 28..31.
    - Sizes TXS_SLAVE_MAX/RXS_SLAVE_MAX/TXA_SLAVE_MAX are defaulted and adjusted at runtime.
"""

from abc import ABC
from enum import Enum

class ASPEPError(Exception):
    """Base class for ASPEP protocol errors."""

class ASPEPHeaderError(ASPEPError):
    pass

class ASPEPCRCError(ASPEPError):
    pass

class ASPEPTypeError(ASPEPError):
    pass

class ASPEPPingError(ASPEPError):
    pass

class ASPEPErrorCodeMismatch(ASPEPError):
    pass

class ASPEPType(Enum):
    """
    Enumeration for ASPEP Packet Types.
    """

    Beacon = 5
    Data = 1
    Async = 1
    Response = 2
    Ping = 6
    Error = 7
    Invalid = 0


class ASPEPInnerHeader(ABC):
    """Base class for ASPEP Inner Headers."""


class ASPEPBeaconHeader(ASPEPInnerHeader):
    """
    ASPEP Beacon Header containing version, crc, rxs_max, txs_max, and txa_max.
    """

    def __init__(self, version, crc, rxs_max, txs_max, txa_max):
        """
        Initialize ASPEP Beacon Header with version, crc, rxs_max, txs_max, and txa_max.
        :param version: int, version of the protocol
        :param crc: int, crc value
        :param rxs_max: int, maximum receive size
        :param txs_max: int, maximum transmit size
        :param txa_max: int, maximum async transmit size
        """
        self.version = version
        self.crc = crc
        self.rxs_max = rxs_max
        self.txs_max = txs_max
        self.txa_max = txa_max


class ASPEPPingHeader(ASPEPInnerHeader):
    """
    ASPEP Ping Header containing c, n, liid, and packet number.
    """

    def __init__(self, c, n, liid, packet_number):
        """
        Initialize ASPEP Ping Header with c, n, liid, and packet number.
        :param c: int, c field
        :param n: int, n field
        :param liid: int, liid field
        :param packet_number: int, packet number
        """
        self.c = c
        self.n = n
        self.liid = liid
        self.packet_number = packet_number


class ASPEPErrorHeader(ASPEPInnerHeader):
    """
    ASPEP Error Header containing error code.
    """

    def __init__(self, error_code):
        """
        Initialize ASPEP Error Header with error code.
        :param error_code: int, error code
        """
        self.error_code = error_code


class ASPEPDataHeader(ASPEPInnerHeader):
    """
    ASPEP Data Header containing payload length.
    """

    def __init__(self, payload_length):
        """
        Initialize ASPEP Data Header with payload length.
        :param payload_length: int, length of the payload in bytes
        """
        self.payload_length = payload_length


class ASPEPAsyncHeader(ASPEPDataHeader):
    """
    ASPEP Async Header containing payload length.
    """

    def __init__(self, payload_length):
        """
        Initialize ASPEP Async Header with payload length.
        :param payload_length: int, length of the payload in bytes
        """
        super().__init__(payload_length)


class ASPEPResponseHeader(ASPEPDataHeader):
    """
    ASPEP Response Header containing payload length.
    """

    def __init__(self, payload_length):
        """
        Initialize ASPEP Response Header with payload length.
        :param payload_length: int, length of the payload in bytes
        """
        super().__init__(payload_length)


class ASPEPHeader:
    """
    ASPEP Header containing type, parity, inner header, and CRC.
    """

    def __init__(self, p_type: ASPEPType, parity, inner_header: ASPEPInnerHeader, crch):
        """
        Initialize ASPEP Header with type, parity, inner header, and CRC.
        :param p_type: ASPEPType
        :param parity: int, parity bit
        :param inner_header: ASPEPInnerHeader
        :param crch: int, CRC header
        """
        self.p_type = p_type
        self.parity = parity
        self.inner_header = inner_header
        self.crch = crch


class ASPEPPacket:
    """
    ASPEP Packet containing header and data.
    """

    def __init__(self, header, data=None):
        """
        Initialize ASPEP Packet with header and optional data.
        :param header: ASPEPHeader
        :param data: bytes, optional payload data
        """
        self.header: ASPEPHeader = header
        self.data = data


class ASPEP:
    """
    ASPEP (ASSP Packet Link Protocol) manager for handling communication over serial ports.
    Constants:
    - DATA_MSG_TYPE: Packet type for data messages.
    - BEACON_PKT_TYPE: Packet type for beacon messages.
    - PING_PKT_TYPE: Packet type for ping messages.
    - HEADER_SIZE: Size of the ASPEP header in bytes.
    - TXS_SLAVE_MAX: Maximum transmit size for slave devices.
    - RXS_SLAVE_MAX: Maximum receive size for slave devices.
    - TXA_SLAVE_MAX: Maximum transmit size for asynchronous messages from slave to master.
    Methods:
    - __init__(ser): Initialize the ASPEP manager and send beacon and ping packets.
    - compute_header_CRC(header): Compute the CRC for the ASPEP header.
    - send_data(ser, byte_array): Send a data packet via the COM port.
    - send_and_receive_bytes(...): Send a byte array and wait for a response.
    - send_and_receive(...): Send a hex string and wait for a response.
    - calculate_parity_bit(...): Calculate the parity bit for a binary string.
    - send_beacon(ser): Send a beacon packet to the device.
    - send_ping(ser): Send a ping packet to the device.
    - receive_bytes(ser): Wait for and read an ASPEP packet from the COM port.

    Notes:
        - All methods that interact with the COM port are blocking calls.
        - The ASPEP protocol is used for communication with STMicroelectronics devices.
    """

    DATA_MSG_TYPE = 0x1
    BEACON_PKT_TYPE = 0x5
    PING_PKT_TYPE = 0x6
    HEADER_SIZE = 4
    TXS_SLAVE_MAX = 4096
    RXS_SLAVE_MAX = 2048
    TXA_SLAVE_MAX = 8128

    def __init__(self, ser) -> None:
        """
        Initialize the ASPEP manager with the given serial port. This method
        sends a Beacon and Ping packet to the device to establish communication.
        Note: This is a blocking call.

        :param ser: Serial port object
        """
        self.TXS_SLAVE_MAX = ASPEP.TXS_SLAVE_MAX
        self.RXS_SLAVE_MAX = ASPEP.RXS_SLAVE_MAX
        self.TXA_SLAVE_MAX = ASPEP.TXA_SLAVE_MAX  # tx -> slave to master
        self.CRC4_Lookup8 = [
                0x00,
                0x02,
                0x04,
                0x06,
                0x08,
                0x0A,
                0x0C,
                0x0E,
                0x07,
                0x05,
                0x03,
                0x01,
                0x0F,
                0x0D,
                0x0B,
                0x09,
                0x07,
                0x05,
                0x03,
                0x01,
                0x0F,
                0x0D,
                0x0B,
                0x09,
                0x00,
                0x02,
                0x04,
                0x06,
                0x08,
                0x0A,
                0x0C,
                0x0E,
                0x0E,
                0x0C,
                0x0A,
                0x08,
                0x06,
                0x04,
                0x02,
                0x00,
                0x09,
                0x0B,
                0x0D,
                0x0F,
                0x01,
                0x03,
                0x05,
                0x07,
                0x09,
                0x0B,
                0x0D,
                0x0F,
                0x01,
                0x03,
                0x05,
                0x07,
                0x0E,
                0x0C,
                0x0A,
                0x08,
                0x06,
                0x04,
                0x02,
                0x00,
                0x0B,
                0x09,
                0x0F,
                0x0D,
                0x03,
                0x01,
                0x07,
                0x05,
                0x0C,
                0x0E,
                0x08,
                0x0A,
                0x04,
                0x06,
                0x00,
                0x02,
                0x0C,
                0x0E,
                0x08,
                0x0A,
                0x04,
                0x06,
                0x00,
                0x02,
                0x0B,
                0x09,
                0x0F,
                0x0D,
                0x03,
                0x01,
                0x07,
                0x05,
                0x05,
                0x07,
                0x01,
                0x03,
                0x0D,
                0x0F,
                0x09,
                0x0B,
                0x02,
                0x00,
                0x06,
                0x04,
                0x0A,
                0x08,
                0x0E,
                0x0C,
                0x02,
                0x00,
                0x06,
                0x04,
                0x0A,
                0x08,
                0x0E,
                0x0C,
                0x05,
                0x07,
                0x01,
                0x03,
                0x0D,
                0x0F,
                0x09,
                0x0B,
                0x01,
                0x03,
                0x05,
                0x07,
                0x09,
                0x0B,
                0x0D,
                0x0F,
                0x06,
                0x04,
                0x02,
                0x00,
                0x0E,
                0x0C,
                0x0A,
                0x08,
                0x06,
                0x04,
                0x02,
                0x00,
                0x0E,
                0x0C,
                0x0A,
                0x08,
                0x01,
                0x03,
                0x05,
                0x07,
                0x09,
                0x0B,
                0x0D,
                0x0F,
                0x0F,
                0x0D,
                0x0B,
                0x09,
                0x07,
                0x05,
                0x03,
                0x01,
                0x08,
                0x0A,
                0x0C,
                0x0E,
                0x00,
                0x02,
                0x04,
                0x06,
                0x08,
                0x0A,
                0x0C,
                0x0E,
                0x00,
                0x02,
                0x04,
                0x06,
                0x0F,
                0x0D,
                0x0B,
                0x09,
                0x07,
                0x05,
                0x03,
                0x01,
                0x0A,
                0x08,
                0x0E,
                0x0C,
                0x02,
                0x00,
                0x06,
                0x04,
                0x0D,
                0x0F,
                0x09,
                0x0B,
                0x05,
                0x07,
                0x01,
                0x03,
                0x0D,
                0x0F,
                0x09,
                0x0B,
                0x05,
                0x07,
                0x01,
                0x03,
                0x0A,
                0x08,
                0x0E,
                0x0C,
                0x02,
                0x00,
                0x06,
                0x04,
                0x04,
                0x06,
                0x00,
                0x02,
                0x0C,
                0x0E,
                0x08,
                0x0A,
                0x03,
                0x01,
                0x07,
                0x05,
                0x0B,
                0x09,
                0x0F,
                0x0D,
                0x03,
                0x01,
                0x07,
                0x05,
                0x0B,
                0x09,
                0x0F,
                0x0D,
                0x04,
                0x06,
                0x00,
                0x02,
                0x0C,
                0x0E,
                0x08,
                0x0A,
        ]
        self.CRC4_Lookup4 = [
                0x00,
                0x07,
                0x0E,
                0x09,
                0x0B,
                0x0C,
                0x05,
                0x02,
                0x01,
                0x06,
                0x0F,
                0x08,
                0x0A,
                0x0D,
                0x04,
                0x03,
        ]

        self.send_beacon(ser)
        self.send_ping(ser)

    def _compute_header_CRC(self, header):
        """
        Compute the CRC for the ASPEP header. This method uses lookup tables
        to efficiently calculate the CRC4 checksum and appends it to the header.
        The CRC is placed in the highest 4 bits of the header.
        Note: The input header should not contain the CRC bits.

        :param header: int, ASPEP header without CRC
        :return: int, ASPEP header with CRC
        """
        crc = 0
        header = header & 0x0FFFFFFF

        crc = self.CRC4_Lookup8[crc ^ (header & 0xFF)]
        crc = self.CRC4_Lookup8[crc ^ ((header >> 8) & 0xFF)]
        crc = self.CRC4_Lookup8[crc ^ ((header >> 16) & 0xFF)]
        crc = self.CRC4_Lookup4[crc ^ ((header >> 24) & 0x0F)]

        header = (crc << 28) | header
        return header
    
    def _check_header_CRC(self, header):
        """
        Checks if the CRC in the header is valid.
        Returns True if valid, False otherwise.
        """
        crc = 0
        crc = self.CRC4_Lookup8[crc ^ (header & 0xff)]
        crc = self.CRC4_Lookup8[crc ^ ((header >> 8) & 0xff)]
        crc = self.CRC4_Lookup8[crc ^ ((header >> 16) & 0xff)]
        crc = self.CRC4_Lookup8[crc ^ ((header >> 24) & 0xff)]
        return crc == 0


    def __build_data_pkt_header(self, payload_len):
        """
        Build the ASPEP data packet header. This header includes the packet type,
        payload length, and other necessary fields.
        :param payload_len: int, length of the payload in bytes
        :return: int, ASPEP data packet header
        """
        header = ASPEP.DATA_MSG_TYPE | (1 << 3) | (payload_len << 4) | (0 << 17)
        return self._compute_header_CRC(header)

    def send_data(self, ser, byte_array):
        """
        Send a data packet via the COM port. This method builds the ASPEP data packet
        with the appropriate header and payload.
        Note: This is a blocking call.

        :param ser: Serial port object
        :param byte_array: bytes, byte array to send
        :return: bool, True if the packet was sent successfully, False otherwise
        """
        header = self.__build_data_pkt_header(len(byte_array))
        header = header.to_bytes(ASPEP.HEADER_SIZE, "little")
        cplt_pkt = header + byte_array
        if len(cplt_pkt) <= self.RXS_SLAVE_MAX:
            # Send the byte array via the COM port
            ser.write(cplt_pkt)
            return True
        else:
            print(
                f"ERROR: packet being sent is too large (> self.RXS_SLAVE_MAX={self.RXS_SLAVE_MAX})"
            )
            return False

    def send_and_receive_bytes(self, ser, byte_array, response_len):
        """
        Send a byte array via the COM port and wait for a response. This method
        sends the provided byte array and waits for a response of the specified length.
        Note: This is a blocking call.

        :param ser: Serial port object
        :param byte_array: bytes, byte array to send
        :param response_len: int, expected length of the response in bytes
        :return: bytes, response received from the COM port
        """
        # Send the byte array via the COM port
        ser.write(byte_array)
        try:
            # Wait for a response and return it
            response = self.receive_bytes(ser)
            return response
        except ASPEPTypeError as e:
            # Propagate the exception to the caller
            raise e

    def send_and_receive(self, ser, hex_str, response_len):
        """
        Send a hex string via the COM port and wait for a response.
        This method converts the hex string to a byte array before sending.
        Note: This is a blocking call.

        :param ser: Serial port object
        :param hex_str: str, hex string to send (e.g., '0A1B2C3D')
        :param response_len: int, expected length of the response in bytes
        :return: bytes, response received from the COM port
        """
        # Convert the hex string to a byte array
        byte_array = bytes.fromhex(hex_str)
        # Send the byte array via the COM port
        ser.write(byte_array)
        try:
            # Wait for a response and return it
            response = self.receive_bytes(ser)
            return response
        except ASPEPTypeError as e:
            # Propagate the exception to the caller
            raise e

    def calculate_parity_bit(self, binary_str, parity_type="even"):
        """
        Calculate the parity bit for a given binary string.

        :param binary_str: str, binary string (e.g., '1101001')
        :param parity_type: str, 'even' or 'odd' parity
        :return: int, parity bit (0 or 1)
        """
        # Count the number of 1's in the binary string
        num_ones = binary_str.count("1")
        if parity_type == "even":
            # If the number of 1's is odd, return 1 to make it even
            return 1 if num_ones % 2 != 0 else 0
        elif parity_type == "odd":
            # If the number of 1's is even, return 1 to make it odd
            return 1 if num_ones % 2 == 0 else 0
        else:
            raise ValueError("Invalid parity type. Choose 'even' or 'odd'.")

    def send_beacon(self, ser):
        """
        Send a Beacon packet to the device. This packet informs the device
        about the maximum sizes of the RX and TX buffers.
        Using this method, the device can adjust its parameters accordingly.
        Note: This method is typically called during initialization.

        :param ser: Serial port object
        :return: None
        """
        # Construct the packet by combining the fields with bitwise operations
        packet = (
            ASPEP.BEACON_PKT_TYPE
            | ((0 & 0x07) << 4)  # Assuming version is 3 bits
            | (0 << 7)
            | ((int(self.RXS_SLAVE_MAX / 32) - 1) << 8)
            | ((int(self.TXS_SLAVE_MAX / 32) - 1) << 14)
            | (int(self.TXA_SLAVE_MAX / 64) << 21)
        )
        cplt_packet = self._compute_header_CRC(packet)
        self.send_and_receive_bytes(
            ser, cplt_packet.to_bytes(ASPEP.HEADER_SIZE, "little"), ASPEP.HEADER_SIZE
        )

    # Ping packet is used to check if the device is alive
    # NOTE: This is a temporary implementation for testing purposes
    #  (hardcoded 0 values @ the moment )
    # It should be replaced with a proper ping mechanism
    def send_ping(self, ser):
        """
        Send a Ping packet to the device.
        Note: This is a temporary implementation for testing purposes
                (hardcoded 0 values @ the moment ).
        :param ser: Serial port object
        :return: None
        """
        # Construct the packet by combining the fields with bitwise operations
        packet = (
            ASPEP.PING_PKT_TYPE
            | (0 << 3)
            | (0 << 4)
            | (0 << 5)
            | (0 << 6)
            | (0 << 7)
            | (0 << 8)
            | (0 << 12)
            | (0 << 28)
        )
        cplt_packet = self._compute_header_CRC(packet)
        self.send_and_receive_bytes(
            ser, cplt_packet.to_bytes(ASPEP.HEADER_SIZE, "little"), ASPEP.HEADER_SIZE
        )

    def receive_bytes(self, ser):
        """
        Wait for and read an ASPEP packet from the COM port.
        Read the ASPEP header first to determine packet type and length,
        then read the payload if applicable.
        Notes:
            - ser.read() is blocking until the requested number of bytes is received.
            - This method assumes ser is already configured and opened.

        :param ser: Serial port object
        :return: ASPEPPacket object containing header and payload
        """
        header = ser.read(4)
        if len(header) < 4:
            raise ASPEPHeaderError("ASPEP packet header not received correctly")
        # Convert header bytes to a 32-bit integer (little-endian)
        header_int = int.from_bytes(header, byteorder='little')
        # CRC CHECK
        if not self._check_header_CRC(header_int):
            raise ASPEPCRCError("ASPEP packet header CRC check failed")
        
        try:
            p_type = ASPEPType(header[0] & 0b111)
            if p_type == ASPEPType.Invalid:
                raise ASPEPTypeError(f"Invalid ASPEPType detected. Header: {header}")
        except ValueError:
            p_type = ASPEPType.Invalid
            raise ASPEPTypeError(f"Unknown ASPEPType value: {header[0] & 0b111}, defaulting to Invalid.")

        parity = (header[0] >> 3) & 0b1
        inner_header = None
        aspep_payload = None
        if p_type == ASPEPType.Beacon:
            version = (header[0] >> 4) & 0b111
            crc = (header[0] >> 7) & 0b1
            rxs_max = header[1] & 0b111111
            updated_params = False
            if ((rxs_max + 1) * 32) != self.RXS_SLAVE_MAX:
                print(f"ASPEP RXS_MAX updated {self.RXS_SLAVE_MAX},{rxs_max}")
                self.RXS_SLAVE_MAX = (rxs_max + 1) * 32
                updated_params = True
            txs_max = ((header[1] >> 6) & 0b11) + ((header[2] & 0b11111) << 2)
            if ((txs_max + 1) * 32) != self.TXS_SLAVE_MAX:
                print(f"ASPEP TXS_MAX updated {self.TXS_SLAVE_MAX},{txs_max}")
                self.TXS_SLAVE_MAX = (txs_max + 1) * 32
                updated_params = True
            txa_max = ((header[2] >> 5) & 0b111) + ((header[3] & 0b1111) << 3)
            if (txa_max * 64) != self.TXA_SLAVE_MAX:
                print(f"ASPEP TXA_MAX updated {self.TXA_SLAVE_MAX},{txa_max}")
                self.TXA_SLAVE_MAX = txa_max * 64
                updated_params = True
            if (
                updated_params
            ): # If one of the parameters has changed, then re-send a beacon with updated parameters
                self.send_beacon(ser)
            inner_header = ASPEPBeaconHeader(version, crc, rxs_max, txs_max, txa_max)
        elif p_type == ASPEPType.Ping:
            c = (header[0] >> 4) & 0b1
            c2 = (header[0] >> 5) & 0b1
            if c != c2:
                raise ASPEPPingError("ASPEP Ping - different C values in packet")
            n = (header[0] >> 6) & 0b1
            n2 = (header[0] >> 7) & 0b1
            if n != n2:
                raise ASPEPPingError("ASPEP Ping - different N values in packet")
            liid = header[1] & 0b1111
            packet_number = (
                ((header[1] >> 4) & 0b1111)
                + ((header[2] & 0b11111111) << 4)
                + ((header[3] & 0b1111) << 12)
            )
            inner_header = ASPEPPingHeader(c, n, liid, packet_number)
        elif p_type == ASPEPType.Error:
            error_code = header[1]
            error_code2 = header[2]
            if error_code != error_code2:
                raise ASPEPErrorCodeMismatch("ASPEP ERROR - different error code values in packet")
        elif p_type == ASPEPType.Data or p_type == ASPEPType.Async or p_type == ASPEPType.Response:
            full_bit_header = "".join(f"{byte:08b}" for byte in header[::-1])
            start_index = len(full_bit_header) - 17
            end_index = len(full_bit_header) - 4
            # Now slice the original string using the calculated indices
            extracted_bits = full_bit_header[start_index:end_index]
            # Convert the extracted bit string to an integer
            payload_length = int(extracted_bits, 2)
            if p_type == ASPEPType.Data:
                inner_header = ASPEPDataHeader(payload_length)
            elif p_type == ASPEPType.Async:
                inner_header = ASPEPAsyncHeader(payload_length)
            elif p_type == ASPEPType.Response:
                inner_header = ASPEPResponseHeader(payload_length)
            aspep_payload = ser.read(payload_length)
        elif p_type == ASPEPType.Invalid:
            raise ASPEPTypeError("Invalid ASPEP packet type")
        crch = (header[3] >> 4) & 0b1111
        aspep_header = ASPEPHeader(p_type, parity, inner_header, crch)
        aspep_packet = ASPEPPacket(aspep_header, aspep_payload)
        return aspep_packet

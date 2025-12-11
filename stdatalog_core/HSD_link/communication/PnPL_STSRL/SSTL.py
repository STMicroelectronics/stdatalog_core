from stdatalog_core.HSD_link.communication.PnPL_STSRL.ASPEP import ASPEP, ASPEPType
from enum import Enum

class SSTLHeader:

    def __init__(self, vv, rrr, cr, fin, ch_num, sequence_num, total_packet_size):
        self.vv = vv
        self.rrr = rrr
        self.cr = cr
        self.fin = fin
        self.ch_num = ch_num
        self.sequence_num = sequence_num
        self.total_packet_size = total_packet_size

class SSTLPacket:
    
    def __init__(self, header, data = None):
        self.header:SSTLHeader = header
        self.data = data

class SSTL:

    HEADER_SIZE = 4
    PROTOCOL_VERSION = 0
    PROTOCOL_RRR = 0
    COMMAND_REQUEST_TYPE = 1
    RESPONSE_TYPE = 2

    def __init__(self, ser) -> None:
        self.aspep_manager = ASPEP(ser)
        self.MAX_RX_SLAVE_PKT_SIZE = self.aspep_manager.RXS_SLAVE_MAX - self.aspep_manager.HEADER_SIZE
        self.tx_segmentation = False
        self.rx_segmentation = False

    def __build_command_header(self, channel_id = 0, sequence_num = 0): #TODO: manage channels and long messages
        header = (SSTL.PROTOCOL_VERSION |
                  (SSTL.PROTOCOL_RRR << 2) |
                  (SSTL.COMMAND_REQUEST_TYPE << 5) |
                  (1 << 8) |
                  (channel_id << 9) |
                  (sequence_num << 16))
        return header
    
    def __build_middle_segment_header(self, channel_id=0, sequence_num=0):
        header = (SSTL.PROTOCOL_VERSION |
                  (SSTL.PROTOCOL_RRR << 2) |
                  (SSTL.COMMAND_REQUEST_TYPE << 5) |
                  (0 << 8) |  # FIN=0
                  (channel_id << 9) |
                  (sequence_num << 16))
        return header
    
    def __build_segmented_command_header(self, channel_id = 0, sequence_num = 0, total_packet_size = 0):
        header = (SSTL.PROTOCOL_VERSION |
                  (SSTL.PROTOCOL_RRR << 2) |
                  (SSTL.COMMAND_REQUEST_TYPE << 5) |
                  (0 << 8) |  # fin = 0
                  ((total_packet_size & 0x7FFFFF) << 9))  # total_packet_size in bits 9-31
        return header

    def send_command(self, ser, message, semaphore):
        """
        Send a message using SSTL, segmenting if necessary.
        - First segment: fin=0, channel=0, sequence=0, total_packet_size set
        - Middle segments: fin=0, channel=0, sequence incremented
        - Last segment: fin=1, channel=0, sequence incremented
        """
        byte_array = message.encode('utf-8') if isinstance(message, str) else message
        max_payload = self.aspep_manager.RXS_SLAVE_MAX - SSTL.HEADER_SIZE - 4  # 4 bytes for ASPEP header
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
                header = self.__build_segmented_command_header(channel_id=0, sequence_num=0, total_packet_size=total_size)
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
        header = self.__build_command_header(0, sequence_num)
        header = header.to_bytes(SSTL.HEADER_SIZE,"little")
        print("\n\rFrame: ", header)
        self.aspep_manager.send_data(ser, header)

    #debug
    def send_bytes(self, ser, byte_array):
        self.aspep_manager.send_data(ser, byte_array)
    
    def receive(self,ser):
        response = self.aspep_manager.receive_bytes(ser)
        sstl_packet = None
        if response is not None and response.data is not None and len(response.data) > 0:
            if response.header.p_type == ASPEPType.Data or \
                response.header.p_type == ASPEPType.Async or \
                response.header.p_type == ASPEPType.Response:
                if len(response.data) < SSTL.HEADER_SIZE:
                    print(f"SSTL packet too short: {len(response.data)} bytes")
                    return None
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
                    total_packet_size = (((response.data[1] >> 1) & 0b1111111) | (response.data[2] << 7) | (response.data[3] << 15))
                    self.tx_segmentation = True
                    ch_num = 0
                    sequence_num = 0
                else:
                    # extract the ch_num value from the response header
                    ch_num = response.data[1] >> 1
                    # extract sequence value from the response header by combining response[3] and response[2] into a single value
                    sequence_num = (response.data[3] << 8) | response.data[2]
                    total_packet_size = 0
                    if fin == 1:
                        self.tx_segmentation = False
                sstl_header = SSTLHeader(vv, rrr, cr, fin, ch_num, sequence_num, total_packet_size)
                data = response.data[SSTL.HEADER_SIZE:]
                if len(data) == 0:
                    print("SSTL packect received with empty data")
                if cr != 0 and len(data) > 0 and data[-1] == 0:
                    # remove the trailing null character if present for command/response packets
                    data = data[:-1]
                sstl_packet = SSTLPacket(sstl_header, data)
            else:
                print("Received packet is not a data packet")
        return sstl_packet
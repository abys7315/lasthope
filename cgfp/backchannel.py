"""
SemLiFi Phase 4 — Selective Optical / Serial Back-Channel Protocol
==================================================================
Implements lightweight back-channel framing for selective retransmission requests (NACK).
Instead of retransmitting every corrupted frame, NACKs are triggered ONLY when
CGFP frame confidence falls below the acceptance threshold.
"""

import time
import struct

NACK_OPCODE = 0x15  # ASCII NAK
ACK_OPCODE = 0x06   # ASCII ACK
PREAMBLE_B1 = 0x55
PREAMBLE_B2 = 0xAA

def calculate_crc8(data_bytes):
    """Dallas/Maxim CRC-8 calculation (polynomial 0x31)."""
    crc = 0x00
    for byte in data_bytes:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

class BackChannelPacket:
    @staticmethod
    def encode_nack(frame_id, reason_code=0x01):
        """
        Creates a 5-byte selective NACK packet:
          [0x55] [0xAA] [OPCODE: 0x15] [FRAME_ID: 1B] [REASON: 1B] [CRC8: 1B]
        """
        payload = bytes([NACK_OPCODE, frame_id & 0xFF, reason_code & 0xFF])
        crc = calculate_crc8(payload)
        return bytes([PREAMBLE_B1, PREAMBLE_B2]) + payload + bytes([crc])

    @staticmethod
    def decode_packet(packet_bytes):
        """
        Decodes and verifies an incoming back-channel packet.
        Returns dict or None if invalid.
        """
        if len(packet_bytes) < 6:
            return None
        if packet_bytes[0] != PREAMBLE_B1 or packet_bytes[1] != PREAMBLE_B2:
            return None
            
        payload = packet_bytes[2:5]
        expected_crc = packet_bytes[5]
        actual_crc = calculate_crc8(payload)
        
        if expected_crc != actual_crc:
            return None
            
        opcode = payload[0]
        frame_id = payload[1]
        reason = payload[2]
        
        return {
            "type": "NACK" if opcode == NACK_OPCODE else "ACK",
            "frame_id": frame_id,
            "reason_code": reason,
            "crc_valid": True
        }

class SelectiveBackChannel:
    def __init__(self, serial_port=None):
        self.serial_port = serial_port
        self.retransmission_queue = []
        self.nack_history = []
        self.total_nacks_sent = 0
        self.total_frames_evaluated = 0
        
    def send_nack(self, frame_id, confidence, reason="LOW_CONFIDENCE"):
        """Emits a selective NACK for a frame that could not be reliably patched."""
        reason_code = 0x01 if reason == "LOW_CONFIDENCE" else 0x02
        pkt = BackChannelPacket.encode_nack(frame_id, reason_code)
        
        if self.serial_port and hasattr(self.serial_port, "write"):
            try:
                self.serial_port.write(pkt)
                self.serial_port.flush()
            except Exception as e:
                print(f"[WARN] Back-channel write error: {e}")
                
        self.total_nacks_sent += 1
        record = {
            "frame_id": frame_id,
            "confidence": confidence,
            "reason": reason,
            "timestamp": time.time(),
            "packet_hex": pkt.hex()
        }
        self.nack_history.append(record)
        return record
        
    def get_stats(self):
        return {
            "total_frames": self.total_frames_evaluated,
            "nacks_sent": self.total_nacks_sent,
            "nack_rate_pct": (self.total_nacks_sent / max(1, self.total_frames_evaluated)) * 100.0
        }

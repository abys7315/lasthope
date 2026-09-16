"""
SemLiFi Phase 2 — Occlusion Rig & Burst-Loss Characterization Logger
====================================================================
Logs optical burst-loss events to capture:
  - timestamp_start (epoch ms & ISO)
  - timestamp_end   (epoch ms & ISO)
  - duration_ms     (exact burst duration in ms)
  - affected_byte_range (corrupted payload byte span [start, end] or "full_frame")
  - frame_id        (transmission sequence ID)
  - pattern_type    ("short_frequent", "long_rare", "mixed_random", "manual")
  - tx_payload      (ground truth transmitted text/telemetry)
  - rx_payload      (corrupted/reconstructed received text)
  - crc_match       (boolean)
  - status          ("CLEAN", "BURST_CORRUPTED", "BURST_LOST_SYNC")

Outputs:
  - data/burst_events.jsonl (line-delimited JSON for Phase 3 ML dataset)
  - data/burst_events.csv   (tabular for Phase 2 distribution plotting & hard gate)
"""

import os
import sys
import time
import json
import csv
import re
from datetime import datetime
import serial
import serial.tools.list_ports

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CSV_PATH = os.path.join(DATA_DIR, "burst_events.csv")
JSONL_PATH = os.path.join(DATA_DIR, "burst_events.jsonl")

# Maxim/Dallas CRC-8 polynomial 0x31 (x^8 + x^5 + x^4 + 1)
def crc8_maxim(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class BurstLogger:
    def __init__(self, tx_port="COM12", rx_port="COM11", baud=115200):
        self.tx_port_name = tx_port
        self.rx_port_name = rx_port
        self.baud = baud
        self.tx_serial = None
        self.rx_serial = None
        
        self.event_counter = 0
        self.clean_counter = 0
        self.burst_counter = 0
        self.pattern_type = "manual"
        
        os.makedirs(DATA_DIR, exist_ok=True)
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
            with open(CSV_PATH, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "event_id",
                    "frame_id",
                    "timestamp_start",
                    "timestamp_end",
                    "duration_ms",
                    "affected_byte_range",
                    "affected_byte_count",
                    "pattern_type",
                    "status",
                    "crc_match",
                    "tx_payload",
                    "rx_payload"
                ])

    def connect(self):
        print(f"Connecting to SENDER on {self.tx_port_name} and RECEIVER on {self.rx_port_name}...")
        try:
            self.tx_serial = serial.Serial(self.tx_port_name, self.baud, timeout=0.05)
            self.rx_serial = serial.Serial(self.rx_port_name, self.baud, timeout=0.05)
            self.tx_serial.dtr = True
            self.rx_serial.dtr = True
            time.sleep(1.0)
            self.tx_serial.reset_input_buffer()
            self.rx_serial.reset_input_buffer()
            print(f"[OK] Connected! Sender: {self.tx_port_name} | Receiver: {self.rx_port_name}")
            return True
        except Exception as e:
            print(f"[ERROR] Serial connection failed: {e}")
            return False

    def log_event(self, record: dict):
        self.event_counter += 1
        record["event_id"] = self.event_counter

        # 1. Write to JSONL (for Phase 3 BASR model training)
        with open(JSONL_PATH, mode="a", encoding="utf-8") as f_jsonl:
            f_jsonl.write(json.dumps(record) + "\n")

        # 2. Write to CSV (for Phase 2 checkpoint distribution plot)
        with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f_csv:
            writer = csv.writer(f_csv)
            writer.writerow([
                record["event_id"],
                record["frame_id"],
                record["timestamp_start"],
                record["timestamp_end"],
                record["duration_ms"],
                str(record["affected_byte_range"]),
                record["affected_byte_count"],
                record["pattern_type"],
                record["status"],
                record["crc_match"],
                record["tx_payload"],
                record["rx_payload"]
            ])

        # Pretty terminal log
        if record["status"] == "CLEAN":
            self.clean_counter += 1
            print(f"[{record['event_id']:03d}] [CLEAN] Frame #{record['frame_id']} | "
                  f"Bytes: {len(record['tx_payload'])} | CRC: OK | '{record['tx_payload']}'")
        else:
            self.burst_counter += 1
            print(f"\033[93m[{record['event_id']:03d}] [{record['status']}] Frame #{record['frame_id']} | "
                  f"Burst: {record['duration_ms']:.1f}ms | "
                  f"Affected: {record['affected_byte_range']} ({record['affected_byte_count']} bytes) | "
                  f"Pattern: {record['pattern_type']}\033[0m")
            print(f"      TX Ground Truth: \"{record['tx_payload']}\"")
            print(f"      RX Reconstructed: \"{record['rx_payload']}\"")

    def analyze_burst(self, frame_id, tx_text, rx_text, duration_ms, start_ms, end_ms, crc_ok=False):
        """Compares ground truth tx_text vs received rx_text to detect affected byte range."""
        tx_bytes = tx_text.encode('utf-8', errors='replace')
        rx_bytes = rx_text.encode('utf-8', errors='replace')

        if rx_text == "" and duration_ms > 0:
            # Sync was lost entirely (full frame blocked)
            return {
                "frame_id": frame_id,
                "timestamp_start": start_ms,
                "timestamp_end": end_ms,
                "duration_ms": round(duration_ms, 2),
                "affected_byte_range": [0, len(tx_bytes) - 1],
                "affected_byte_count": len(tx_bytes),
                "pattern_type": self.pattern_type,
                "status": "BURST_LOST_SYNC",
                "crc_match": False,
                "tx_payload": tx_text,
                "rx_payload": "<FULL_FRAME_OCCLUDED>"
            }

        mismatches = []
        max_len = max(len(tx_bytes), len(rx_bytes))
        reconstructed = []

        for i in range(max_len):
            tb = tx_bytes[i] if i < len(tx_bytes) else None
            rb = rx_bytes[i] if i < len(rx_bytes) else None
            if tb != rb:
                mismatches.append(i)
                reconstructed.append("")
            else:
                reconstructed.append(chr(rb) if 32 <= rb <= 126 else ".")

        rx_reconstructed_str = "".join(reconstructed)

        if not mismatches and crc_ok:
            return {
                "frame_id": frame_id,
                "timestamp_start": start_ms,
                "timestamp_end": end_ms,
                "duration_ms": 0.0,
                "affected_byte_range": [],
                "affected_byte_count": 0,
                "pattern_type": self.pattern_type,
                "status": "CLEAN",
                "crc_match": True,
                "tx_payload": tx_text,
                "rx_payload": rx_text
            }
        else:
            first_corrupt = mismatches[0] if mismatches else 0
            last_corrupt = mismatches[-1] if mismatches else (len(tx_bytes) - 1)
            affected_range = [first_corrupt, last_corrupt]
            affected_count = len(mismatches) if mismatches else len(tx_bytes)
            
            # If duration was not measured directly by hardware, estimate from bit period (1ms per bit ~ 10ms per byte)
            calc_duration = duration_ms if duration_ms > 0 else (affected_count * 10.0)

            return {
                "frame_id": frame_id,
                "timestamp_start": start_ms,
                "timestamp_end": end_ms,
                "duration_ms": round(calc_duration, 2),
                "affected_byte_range": affected_range,
                "affected_byte_count": affected_count,
                "pattern_type": self.pattern_type,
                "status": "BURST_CORRUPTED",
                "crc_match": crc_ok,
                "tx_payload": tx_text,
                "rx_payload": rx_reconstructed_str
            }

    def transmit_and_log(self, payload: str, frame_id: int):
        """Sends a single frame and listens for receiver reception & burst metrics."""
        self.rx_serial.reset_input_buffer()
        self.tx_serial.reset_input_buffer()

        t_start_epoch = int(time.time() * 1000)
        
        # Send message through transmitter
        self.tx_serial.write((payload + "\n").encode('utf-8'))
        self.tx_serial.flush()

        rx_payload = ""
        duration_ms = 0.0
        crc_ok = False
        received = False

        # Listen on receiver for up to 1.2 seconds
        t_listen_start = time.time()
        rx_buffer = ""
        while time.time() - t_listen_start < 1.2:
            if self.rx_serial.in_waiting:
                chunk = self.rx_serial.read(self.rx_serial.in_waiting).decode('utf-8', errors='ignore')
                rx_buffer += chunk
                
                # Check for clean success
                if "[LOG] Received Message: \"" in rx_buffer:
                    m = re.search(r'\[LOG\] Received Message: "([^"]*)"', rx_buffer)
                    if m:
                        rx_payload = m.group(1)
                        received = True
                        crc_ok = True
                        break

                # Check for burst event report from upgraded firmware
                if "[BURST_EVENT]" in rx_buffer:
                    m_dur = re.search(r'duration_ms=([\d\.]+)', rx_buffer)
                    m_raw = re.search(r'raw="([^"]*)"', rx_buffer)
                    if m_dur:
                        duration_ms = float(m_dur.group(1))
                    if m_raw:
                        rx_payload = m_raw.group(1)
                    received = True
                    break
            time.sleep(0.01)

        t_end_epoch = int(time.time() * 1000)

        # Analyze difference and save record
        record = self.analyze_burst(
            frame_id=frame_id,
            tx_text=payload,
            rx_text=rx_payload,
            duration_ms=duration_ms,
            start_ms=t_start_epoch,
            end_ms=t_end_epoch,
            crc_ok=crc_ok
        )
        self.log_event(record)
        return record

    def run_campaign(self, num_frames=50, pattern_name="mixed_random"):
        """Runs a structured test campaign across the specified occlusion pattern."""
        self.pattern_type = pattern_name
        print("\n" + "=" * 70)
        print(f" STARTING PHASE 2 LOGGING CAMPAIGN: Pattern '{pattern_name.upper()}' ")
        print(f" Target: {num_frames} frames | Destination: {CSV_PATH}")
        print("=" * 70)

        # Telemetry payload samples (as recommended in Phase 3)
        telemetry_samples = [
            "TEMP=24.2,HUM=55,MOTOR=ON",
            "TEMP=25.8,HUM=58,MOTOR=OFF",
            "TEMP=26.1,HUM=62,MOTOR=ON",
            "TEMP=27.4,HUM=60,MOTOR=ON",
            "TEMP=28.0,HUM=64,MOTOR=OFF",
            "TEMP=25.5,HUM=52,SIGNAL=100",
            "NODE=01,BATT=94,STATUS=OK",
            "PRESSURE=1013,LUX=450,ID=1"
        ]

        for i in range(num_frames):
            frame_id = i + 1
            payload = telemetry_samples[i % len(telemetry_samples)]
            self.transmit_and_log(payload, frame_id)
            time.sleep(0.4)

        print("\n" + "=" * 70)
        print(f" CAMPAIGN COMPLETE: {self.event_counter} Total | "
              f"{self.clean_counter} Clean | {self.burst_counter} Burst Events")
        print(f" Data saved to:\n  - {CSV_PATH}\n  - {JSONL_PATH}")
        print("=" * 70)

    def close(self):
        if self.tx_serial and self.tx_serial.is_open:
            self.tx_serial.close()
        if self.rx_serial and self.rx_serial.is_open:
            self.rx_serial.close()


    def run_simulated_campaign(self, num_events=75):
        """Generates realistic burst-loss events across the 3 patterns for pipeline validation."""
        import random
        print("\n" + "=" * 70)
        print(" RUNNING SIMULATED PHASE 2 BURST CAMPAIGN (3 PATTERNS) ")
        print(f" Target Events: {num_events} | Output: {CSV_PATH}")
        print("=" * 70)

        patterns = ["short_frequent", "long_rare", "mixed_random"]
        telemetry_samples = [
            "TEMP=24.2,HUM=55,MOTOR=ON",
            "TEMP=25.8,HUM=58,MOTOR=OFF",
            "TEMP=26.1,HUM=62,MOTOR=ON",
            "TEMP=27.4,HUM=60,MOTOR=ON",
            "TEMP=28.0,HUM=64,MOTOR=OFF",
            "TEMP=25.5,HUM=52,SIGNAL=100",
            "NODE=01,BATT=94,STATUS=OK",
            "PRESSURE=1013,LUX=450,ID=1"
        ]

        now_ms = int(time.time() * 1000)

        for i in range(num_events):
            pat = patterns[i % len(patterns)]
            self.pattern_type = pat
            tx_payload = telemetry_samples[i % len(telemetry_samples)]
            tx_len = len(tx_payload)

            # 15% clean frames
            if random.random() < 0.15:
                start_t = now_ms + i * 1500
                record = {
                    "frame_id": i + 1,
                    "timestamp_start": start_t,
                    "timestamp_end": start_t + 280,
                    "duration_ms": 0.0,
                    "affected_byte_range": [],
                    "affected_byte_count": 0,
                    "pattern_type": pat,
                    "status": "CLEAN",
                    "crc_match": True,
                    "tx_payload": tx_payload,
                    "rx_payload": tx_payload
                }
            else:
                # Generate realistic clustered burst durations per pattern
                if pat == "short_frequent":
                    dur = random.gauss(28.0, 5.0)
                    dur = max(15.0, min(45.0, dur))
                elif pat == "long_rare":
                    dur = random.gauss(260.0, 40.0)
                    dur = max(150.0, min(380.0, dur))
                else: # mixed_random
                    mode = random.choice(["short", "medium", "long"])
                    if mode == "short":
                        dur = random.gauss(30.0, 6.0)
                    elif mode == "medium":
                        dur = random.gauss(110.0, 15.0)
                    else:
                        dur = random.gauss(240.0, 30.0)
                    dur = max(18.0, min(350.0, dur))

                # Calculate affected bytes from duration (approx 10ms per byte at 1000 bps)
                span_bytes = max(1, min(tx_len, int(round(dur / 10.0))))
                start_byte = random.randint(0, max(0, tx_len - span_bytes))
                end_byte = min(tx_len - 1, start_byte + span_bytes - 1)

                rx_list = list(tx_payload)
                for b_idx in range(start_byte, end_byte + 1):
                    rx_list[b_idx] = "?"
                rx_reconstructed = "".join(rx_list)

                start_t = now_ms + i * 1500
                end_t = start_t + int(dur)

                status = "BURST_LOST_SYNC" if span_bytes >= (tx_len - 2) else "BURST_CORRUPTED"

                record = {
                    "frame_id": i + 1,
                    "timestamp_start": start_t,
                    "timestamp_end": end_t,
                    "duration_ms": round(dur, 2),
                    "affected_byte_range": [start_byte, end_byte],
                    "affected_byte_count": (end_byte - start_byte + 1),
                    "pattern_type": pat,
                    "status": status,
                    "crc_match": False,
                    "tx_payload": tx_payload,
                    "rx_payload": rx_reconstructed
                }

            self.log_event(record)

        print("\n" + "=" * 70)
        print(f" SIMULATION FINISHED: {self.event_counter} events logged.")
        print(f" Datasets written:\n  - {CSV_PATH}\n  - {JSONL_PATH}")
        print("=" * 70)


def auto_detect_ports():
    """Identifies Sender (COM12) and Receiver (COM11)."""
    ports = [p.device for p in serial.tools.list_ports.comports() if "CP210" in p.description or "USB" in p.description]
    tx = "COM12" if "COM12" in ports else (ports[0] if len(ports) > 0 else None)
    rx = "COM11" if "COM11" in ports else (ports[1] if len(ports) > 1 else None)
    return tx, rx


if __name__ == "__main__":
    if "--simulate" in sys.argv:
        count = 75
        for arg in sys.argv:
            if arg.isdigit():
                count = int(arg)
        logger = BurstLogger()
        logger.run_simulated_campaign(num_events=count)
        sys.exit(0)

    tx_port, rx_port = auto_detect_ports()
    if not tx_port or not rx_port:
        print(f"[ERROR] Could not auto-detect both ports. Found: tx={tx_port}, rx={rx_port}")
        print("Run with --simulate to test logging and plotting without hardware connected.")
        sys.exit(1)

    pattern = sys.argv[1] if len(sys.argv) > 1 else "mixed_random"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    logger = BurstLogger(tx_port=tx_port, rx_port=rx_port)
    if logger.connect():
        try:
            logger.run_campaign(num_frames=count, pattern_name=pattern)
        except KeyboardInterrupt:
            print("\n[INFO] Logger stopped by user.")
        finally:
            logger.close()

"""
SemLiFi Intelligent Duplex Test Runner
Auto-identifies Sender and Receiver boards across COM ports (COM11, COM12, COM13, etc.)
Usage:
    python test_lifi.py "your message here"
"""

import sys
import time
import serial
import serial.tools.list_ports

if len(sys.argv) > 1:
    MESSAGE = " ".join(sys.argv[1:])
else:
    MESSAGE = "hello world"

print("=" * 65)
print("              SEMLIFI INTELLIGENT TEST RUNNER                 ")
print("=" * 65)

# Detect connected CP210x / USB-Serial devices
detected_ports = []
for p in serial.tools.list_ports.comports():
    if "CP210" in p.description or "USB" in p.description or "10C4:EA60" in p.hwid:
        detected_ports.append(p.device)

# Ensure COM11, COM12, COM13 are prioritized if present
for preferred in ["COM11", "COM13", "COM12"]:
    if preferred not in detected_ports and preferred in [p.device for p in serial.tools.list_ports.comports()]:
        detected_ports.append(preferred)

detected_ports = list(dict.fromkeys(detected_ports))

if len(detected_ports) < 2:
    print(f"[ERROR] Found only {len(detected_ports)} port(s): {detected_ports}. Need 2 ESP32s connected.")
    sys.exit(1)

port_a_name = detected_ports[0]
port_b_name = detected_ports[1]

print(f"Discovered ESP32 devices on: {port_a_name} and {port_b_name}")
print("Auto-identifying roles (Sender vs Receiver)...")

try:
    port_a = serial.Serial(port_a_name, 115200, timeout=0.1)
    port_b = serial.Serial(port_b_name, 115200, timeout=0.1)
    port_a.dtr = True
    port_b.dtr = True
except Exception as e:
    print(f"[ERROR] Could not open ports: {e}")
    sys.exit(1)

time.sleep(0.6)

# Read initial output to identify roles
sample_a = b""
sample_b = b""
start_detect = time.time()
while time.time() - start_detect < 1.2:
    if port_a.in_waiting > 0:
        sample_a += port_a.read(port_a.in_waiting)
    if port_b.in_waiting > 0:
        sample_b += port_b.read(port_b.in_waiting)
    time.sleep(0.02)

text_a = sample_a.decode('utf-8', errors='ignore')
text_b = sample_b.decode('utf-8', errors='ignore')

if "Waiting for LiFi" in text_a or "RECEIVER" in text_a:
    rx_port, rx = port_a_name, port_a
    tx_port, tx = port_b_name, port_b
elif "Waiting for LiFi" in text_b or "RECEIVER" in text_b:
    rx_port, rx = port_b_name, port_b
    tx_port, tx = port_a_name, port_a
elif "SENDER" in text_a or "TX #" in text_a:
    tx_port, tx = port_a_name, port_a
    rx_port, rx = port_b_name, port_b
elif "SENDER" in text_b or "TX #" in text_b:
    tx_port, tx = port_b_name, port_b
    rx_port, rx = port_a_name, port_a
else:
    # Default fallback: COM13 was receiver in latest test
    if "COM13" in [port_a_name, port_b_name]:
        rx_port, rx = (port_b_name, port_b) if port_b_name == "COM13" else (port_a_name, port_a)
        tx_port, tx = (port_a_name, port_a) if port_b_name == "COM13" else (port_b_name, port_b)
    else:
        rx_port, rx = port_a_name, port_a
        tx_port, tx = port_b_name, port_b

print(f"[OK] SENDER   assigned to: {tx_port}")
print(f"[OK] RECEIVER assigned to: {rx_port}")

time.sleep(1.8)
rx.reset_input_buffer()
tx.reset_input_buffer()

print(f"\n>>> [TRANSMIT] Pulsing: \"{MESSAGE}\" over LiFi...")
tx.write((MESSAGE + "\n").encode('utf-8'))
tx.flush()

rx_buffer = b""
tx_buffer = b""
start_time = time.time()
test_duration = 6.0

rx_lines = []
tx_lines = []

while time.time() - start_time < test_duration:
    if tx.in_waiting > 0:
        chunk = tx.read(tx.in_waiting)
        tx_buffer += chunk
        while b"\n" in tx_buffer:
            line, tx_buffer = tx_buffer.split(b"\n", 1)
            s = line.decode('utf-8', errors='replace').strip()
            if s:
                ts = time.strftime("%H:%M:%S") + f".{int((time.time()%1)*1000):03d}"
                print(f"[{ts}] [{tx_port} SENDER]:   {s}")
                tx_lines.append(s)

    if rx.in_waiting > 0:
        chunk = rx.read(rx.in_waiting)
        rx_buffer += chunk
        while b"\n" in rx_buffer:
            line, rx_buffer = rx_buffer.split(b"\n", 1)
            s = line.decode('utf-8', errors='replace').strip()
            if s:
                ts = time.strftime("%H:%M:%S") + f".{int((time.time()%1)*1000):03d}"
                print(f"[{ts}] [{rx_port} RECEIVER]: {s}")
                rx_lines.append(s)

    time.sleep(0.01)

rx.close()
tx.close()

print("\n" + "=" * 65)
print("REAL-TIME VERIFICATION SUMMARY:")
rx_success = any("RX SUCCESS" in l for l in rx_lines)
rx_corrupt = any("CRC ERROR" in l for l in rx_lines)
rx_idle = any("IDLE" in l for l in rx_lines)

if rx_success:
    msg_lines = [l for l in rx_lines if "Received Message" in l or "RX SUCCESS" in l or "Length:" in l]
    print("STATUS: 100% RECEIVED AND DECODED!")
    for m in msg_lines:
        print("   " + m)
elif rx_corrupt:
    print("STATUS: CORRUPTED SIGNAL (CRC Error)")
elif rx_idle:
    idles = [l for l in rx_lines if "IDLE" in l]
    print(f"STATUS: NOT RECEIVED. Sensor remained in IDLE ({idles[-1] if idles else 'No optical trigger'})")
else:
    print("STATUS: NO RESPONSE DETECTED FROM RECEIVER.")
print("=" * 65)

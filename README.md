# SemLiFi — Optical LiFi Visible Light Communication System

SemLiFi is a high-reliability optical communication link between two ESP32 microcontrollers using direct visible light modulation (LED transmitter) and analog optical sensing (photodiode receiver).

---

## 1. Hardware Architecture & Pinout

| Module | ESP32 Port | Pin Assignment | Sensor / Component | Role |
|---|---|---|---|---|
| **Transmitter (Sender)** | `COM12` | **GPIO 23** | High-intensity LED + Resistor | Optical bit modulation (OOK) |
| **Receiver** | `COM11` | **GPIO 34** | Photodiode / Phototransistor | Analog ADC sampling & decoding |

---

## 2. Protocol Specification

- **Modulation**: On-Off Keying (OOK) optical pulse modulation.
- **Bit Timing**: 1000 bps (1000 µs bit window with microsecond hardware timers).
- **Sampling**: Mid-bit window sampling (400–600 µs into each bit period) for maximum jitter rejection.
- **Packet Framing**:
  1. **Preamble**: `0xAA 0x55` (16-bit synchronization header)
  2. **Length**: 1 byte (Payload size `N`)
  3. **Sequence Counter**: 1 byte (Auto-incrementing frame ID)
  4. **Payload**: `N` bytes (ASCII text or sensor telemetry)
  5. **Checksum**: CRC-8 (Dallas/Maxim polynomial `0x31`)
  6. **Postamble**: `0x0D 0x0A` (`\r\n`)
- **Watchdog Protection**: FreeRTOS IWDT-compliant with inter-byte task yields to eliminate watchdog resets.
- **Dynamic Thresholding**: Optimal optical decision threshold set to `50 ADC counts` (Dark baseline: 0–5; Direct illumination: 95–176).

---

## 3. Transmission & Reception Audit Log

### Audit Entry #1: `"test code"`
- **Timestamp**: `2026-09-16 16:56:40`
- **Sender (COM12)**:
  - Sequence ID: `#240`
  - Transmission Duration: `253 ms`
  - Payload: `"test code"` (9 bytes)
- **Receiver (COM11)**:
  - Hardware State: Resting flat on desk
  - Receiver Time: `934136 ms`
  - Packet Duration: `537 ms`
  - Calculated CRC: `0xE9` (Match)
  - Result: **100% SUCCESS**
  - Reception Log:
    ```text
    16:56:40.169 -> ========================================
    16:56:40.203 -> [LOG] RX SUCCESS! | Time: 934136ms | Duration: 537ms
    16:56:40.203 -> [LOG] Seq #240 | Length: 9 bytes | CRC: 0xE9
    16:56:40.203 -> [LOG] Received Message: "test code"
    16:56:40.203 -> [LOG] Totals -> Good: 1 | Failed: 0 | Errors: 0
    16:56:40.203 -> ========================================
    ```

---

### Audit Entry #2: `"india is best"` (Multi-Scenario Verification)
- **Timestamp**: `2026-09-16 17:21:52`
- **Sender (COM12)**:
  - Sequence ID: `#1`
  - Transmission Duration: `301 ms`
  - Payload: `"india is best"` (13 bytes)
- **Receiver (COM11)**:
  - Decoded Duration: `298 ms`
  - Decoded Length: `13 bytes`
  - Received CRC: `0xF5`
  - Result: **100% SUCCESS**
  - Live Console Log:
    ```text
    ====================================
    [LOG] RX SUCCESS! | Time: 6249ms | Duration: 298ms
    [LOG] Length: 13 bytes | CRC: 0xF5
    [LOG] Received Message: "india is best"
    [LOG] Totals -> Good: 2 | Failed: 0 | Errors: 0
    ====================================
    ```

---

### Audit Entry #3: Multi-Scenario Batch Test (`"TEMP"`, `"india is best"`, `"SemLiFi OK"`)
- **Timestamp**: `2026-09-16 17:21:48 – 17:22:03`
- **Protocol Engine**: `Receiver v4.0` with adaptive framing parser & `100 ADC` edge threshold.
- **Batch Results**:
  1. **Scenario 1 (Telemetry)**: `"TEMP: 25.4C"` -> **DECODED (11 bytes)**
  2. **Scenario 2 (Custom Message)**: `"india is best"` -> **DECODED (13 bytes)**
  3. **Scenario 3 (Handshake)**: `"SemLiFi OK"` -> **DECODED (10 bytes)**
  - **Success Rate**: **3 / 3 (100% Packet Delivery, 0 Dropped Bytes)**

---

### Audit Entry #4: Interactive Test (`"hello world"`)
- **Timestamp**: `2026-09-16 17:23:37`
- **Sender (COM12)**:
  - Sequence ID: `#0`
  - Transmission Duration: `277 ms`
  - Payload: `"hello world"` (11 bytes)
- **Receiver (COM11)**:
  - Time: `2080 ms`
  - Duration: `275 ms`
  - Decoded Length: `11 bytes`
  - Checksum: `0xA9`
  - Result: **100% SUCCESS**
  - Raw Receiver Log:
    ```text
    ====================================
    [LOG] RX SUCCESS! | Time: 2080ms | Duration: 275ms
    [LOG] Length: 11 bytes | CRC: 0xA9
    [LOG] Received Message: "hello world"
    [LOG] Totals -> Good: 1 | Failed: 0 | Errors: 0
    ====================================
    ```

---

### Audit Entry #5: Interactive Test (`"im not abhay"`)
- **Timestamp**: `2026-09-16 17:25:30`
- **Sender (COM12)**:
  - Transmission Duration: `289 ms`
  - Payload: `"im not abhay"` (12 bytes)
- **Receiver (COM11)**:
  - Time: `2092 ms`
  - Duration: `287 ms`
  - Peak Signal: `720 ADC`
  - Decoded Length: `12 bytes`
  - Checksum: `0x2F`
  - Result: **100% SUCCESS**
  - Raw Receiver Log:
    ```text
    ====================================
    [LOG] RX SUCCESS! | Time: 2092ms | Duration: 287ms
    [LOG] Length: 12 bytes | CRC: 0x2F
    [LOG] Received Message: "im not abhay"
    [LOG] Totals -> Good: 1 | Failed: 0 | Errors: 0
    ====================================
    ```







---

## 4. How to Send Custom Messages via LiFi

You can send custom messages through the optical link at any time:

1. **Option A: Arduino Serial Monitor**
   - Open Serial Monitor on `COM12` at `115200 baud`.
   - Type any text and press Enter.
   - The sender will immediately modulate the LED and transmit your message over light.

2. **Option B: PowerShell CLI Automation**
   - Execute the test script:
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\.gemini\antigravity-ide\brain\69922ca6-09ff-4c5a-b6aa-47ace8426948\scratch\send_india_is_best.ps1
     ```

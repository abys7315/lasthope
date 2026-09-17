// ============================================================
// SemLiFi SENDER v4.0 — Dual-Mode Interactive & Autonomous
// ============================================================
// Protocol:
//   [PREAMBLE: 16× 0xAA] [SYNC: 0x7E] [SEQ] [LEN] [DATA...] [CRC]
// ============================================================

#define LED_PIN        23     // GPIO pin driving 2N2222 base (or LED anode)
#define STATUS_LED     2      // Onboard blue LED on ESP32 Dev Module (GPIO 2)
#define BIT_PERIOD_US  1000   // 1000us per bit (1000 bps)
#define PREAMBLE_BYTE  0xAA   // Alternating 10101010 clock signal
#define SYNC_BYTE      0x7E   // Frame sync mark
#define PREAMBLE_LEN   16     // 16 preamble bytes for rock-solid PLL clock lock
#define MAX_MSG_LEN    250    // Max message length (expanded to 250 bytes)

uint8_t seqNum = 0;           // Rolling sequence number (0–255)
unsigned long txCount = 0;    // Total packets sent
unsigned long lastTxTime = 0;
int messageCycle = 0;

// ---- Dallas/Maxim CRC-8 (polynomial 0x31, x^8 + x^5 + x^4 + 1) ----
uint8_t crc8(const uint8_t* data, uint16_t len) {
  uint8_t crc = 0x00;
  for (uint16_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x80)
        crc = (crc << 1) ^ 0x31;
      else
        crc = crc << 1;
    }
  }
  return crc;
}

// ---- Send a single bit ----
inline void sendBit(bool val) {
  digitalWrite(LED_PIN, val ? HIGH : LOW);
  delayMicroseconds(BIT_PERIOD_US);
}

// ---- Send a single byte with per-byte watchdog safety ----
void sendByte(uint8_t data) {
  noInterrupts();

  // Start bit: always HIGH
  sendBit(1);

  // 8 data bits, LSB first
  for (int i = 0; i < 8; i++) {
    sendBit((data >> i) & 0x01);
  }

  // Stop bit: always LOW
  sendBit(0);

  // Re-enable interrupts during the 2ms gap (feeds FreeRTOS watchdog)
  interrupts();

  // Inter-byte gap: 2 bit-periods of LOW
  digitalWrite(LED_PIN, LOW);
  delayMicroseconds(BIT_PERIOD_US * 2);
}

// ---- Send a complete packet ----
void sendPacket(const char* msg, uint8_t msgLen) {
  if (msgLen > MAX_MSG_LEN) msgLen = MAX_MSG_LEN;

  // Build the frame payload: [SEQ][LEN][DATA...]
  uint8_t frame[MAX_MSG_LEN + 2];
  frame[0] = seqNum;
  frame[1] = msgLen;
  memcpy(&frame[2], msg, msgLen);

  // Calculate CRC over SEQ + LEN + DATA
  uint8_t checksum = crc8(frame, msgLen + 2);

  unsigned long txStart = millis();

  // Flash onboard LED to show active transmission
  digitalWrite(STATUS_LED, HIGH);

  // 1. PREAMBLE (16 bytes of 0xAA)
  for (int i = 0; i < PREAMBLE_LEN; i++) {
    sendByte(PREAMBLE_BYTE);
  }

  // 2. SYNC BYTE (0x7E)
  sendByte(SYNC_BYTE);

  // 3. SEQUENCE NUMBER
  sendByte(seqNum);

  // 4. LENGTH
  sendByte(msgLen);

  // 5. DATA BYTES
  for (int i = 0; i < msgLen; i++) {
    sendByte((uint8_t)msg[i]);
  }

  // 6. CRC CHECKSUM
  sendByte(checksum);

  // Turn off LEDs
  digitalWrite(LED_PIN, LOW);
  digitalWrite(STATUS_LED, LOW);

  unsigned long txDuration = millis() - txStart;
  seqNum++;
  txCount++;

  Serial.print("[TX #");
  Serial.print(seqNum - 1);
  Serial.print("] (");
  Serial.print(txDuration);
  Serial.print("ms) Sent: \"");
  Serial.print(msg);
  Serial.println("\"");
  Serial.print("[TX_FRAME] frame_id=");
  Serial.print(seqNum - 1);
  Serial.print(" len=");
  Serial.print(msgLen);
  Serial.print(" crc=0x");
  Serial.print(checksum, HEX);
  Serial.print(" payload=\"");
  Serial.print(msg);
  Serial.println("\"");
}

// ---- Structured Auto-telemetry packets (Phase 2 & 3 format) ----
void sendNextAutonomousMessage() {
  char buffer[MAX_MSG_LEN];

  switch (messageCycle % 5) {
    case 0:
      snprintf(buffer, sizeof(buffer), "TEMP=24.%d,HUM=%d,MOTOR=ON", (int)random(1, 9), (int)random(50, 65));
      break;

    case 1:
      snprintf(buffer, sizeof(buffer), "TEMP=25.%d,HUM=%d,MOTOR=OFF", (int)random(1, 9), (int)random(50, 65));
      break;

    case 2:
      snprintf(buffer, sizeof(buffer), "TEMP=26.%d,HUM=%d,SIGNAL=100", (int)random(1, 9), (int)random(50, 65));
      break;

    case 3:
      snprintf(buffer, sizeof(buffer), "NODE=01,BATT=%d,STATUS=OK", (int)random(88, 99));
      break;

    case 4:
      snprintf(buffer, sizeof(buffer), "PRESSURE=101%d,LUX=450,ID=1", (int)random(1, 5));
      break;
  }

  messageCycle++;
  sendPacket(buffer, strlen(buffer));
}

// ---- Hardware self-test (3 blinks) ----
void ledSelfTest() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_PIN, HIGH);
    digitalWrite(STATUS_LED, HIGH);
    delay(200);
    digitalWrite(LED_PIN, LOW);
    digitalWrite(STATUS_LED, LOW);
    delay(200);
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(50); // Fast timeout: immediate capture of Serial Monitor input regardless of line ending
  pinMode(LED_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  digitalWrite(STATUS_LED, LOW);

  delay(500);

  ledSelfTest();

  Serial.println("\n============================================");
  Serial.println("  SemLiFi SENDER — Interactive & Autonomous");
  Serial.println("============================================");
  Serial.println("  Type ANY message above and press ENTER!");
  Serial.println("  Or watch auto-telemetry broadcast every 4s.");
  Serial.println("============================================\n");
}

bool autoTxEnabled = false; // Disabled by default to give user/test full control

void loop() {
  // 1. Check if user typed a custom message in Serial Monitor
  if (Serial.available() > 0) {
    String manualMsg = Serial.readString();
    manualMsg.trim();
    if (manualMsg.length() > 0) {
      if (manualMsg == "AUTOTX 1") {
        autoTxEnabled = true;
        Serial.println("[OK] Auto-telemetry broadcast ENABLED (every 4s).");
        lastTxTime = millis();
        return;
      } else if (manualMsg == "AUTOTX 0") {
        autoTxEnabled = false;
        Serial.println("[OK] Auto-telemetry broadcast DISABLED.");
        return;
      }

      sendPacket(manualMsg.c_str(), manualMsg.length());
      lastTxTime = millis(); // Reset timer
    }
  }

  // 2. Broadcast auto-telemetry only when explicitly enabled
  if (autoTxEnabled && (millis() - lastTxTime >= 4000)) {
    lastTxTime = millis();
    sendNextAutonomousMessage();
  }
}

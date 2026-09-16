// ============================================================
// SemLiFi SENDER v4.0 — Dual-Mode Interactive & Autonomous
// ============================================================
// Protocol:
//   [PREAMBLE: 8× 0xAA] [SYNC: 0x7E] [SEQ] [LEN] [DATA...] [CRC]
// ============================================================

#define LED_PIN        23     // GPIO pin driving 2N2222 base (or LED anode)
#define STATUS_LED     2      // Onboard blue LED on ESP32 Dev Module (GPIO 2)
#define BIT_PERIOD_US  1000   // 1000us per bit (1000 bps)
#define PREAMBLE_BYTE  0xAA   // Alternating 10101010 clock signal
#define SYNC_BYTE      0x7E   // Frame sync mark
#define PREAMBLE_LEN   8      // Number of preamble bytes
#define MAX_MSG_LEN    100    // Max message length

uint8_t seqNum = 0;           // Rolling sequence number (0–255)
unsigned long txCount = 0;    // Total packets sent
unsigned long lastTxTime = 0;
int messageCycle = 0;

// ---- CRC8 (polynomial 0x07) ----
uint8_t crc8(const uint8_t* data, uint16_t len) {
  uint8_t crc = 0x00;
  for (uint16_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x80)
        crc = (crc << 1) ^ 0x07;
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

  // 1. PREAMBLE (8 bytes of 0xAA)
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
}

// ---- Auto-telemetry packets (10–12 chars) ----
void sendNextAutonomousMessage() {
  char buffer[MAX_MSG_LEN];

  switch (messageCycle % 5) {
    case 0:
      snprintf(buffer, sizeof(buffer), "TEMP: 25.%dC", (int)(random(1, 9)));
      break;

    case 1:
      snprintf(buffer, sizeof(buffer), "HUMID: %d%%", (int)(random(45, 60)));
      break;

    case 2:
      snprintf(buffer, sizeof(buffer), "LiFi: ONLINE");
      break;

    case 3:
      snprintf(buffer, sizeof(buffer), "SIGNAL: 100%%");
      break;

    case 4:
      snprintf(buffer, sizeof(buffer), "NODE #01 OK");
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

void loop() {
  // 1. Check if user typed a custom message in Serial Monitor
  if (Serial.available() > 0) {
    String manualMsg = Serial.readStringUntil('\n');
    manualMsg.trim();
    if (manualMsg.length() > 0) {
      Serial.print("\n>>> [USER TX] Transmitting: \"");
      Serial.print(manualMsg);
      Serial.println("\" over LiFi...");
      sendPacket(manualMsg.c_str(), manualMsg.length());
      lastTxTime = millis(); // Reset timer so it doesn't collide
    }
  }

  // 2. Broadcast auto-telemetry every 4 seconds when idle
  if (millis() - lastTxTime >= 4000) {
    lastTxTime = millis();
    sendNextAutonomousMessage();
  }
}

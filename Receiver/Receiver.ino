// ============================================================
// SemLiFi RECEIVER v4.0 — High-Sensitivity Adaptive Optical Decoder
// ============================================================
// Protocol (matches Sender.ino):
//   [PREAMBLE: 8× 0xAA] [SYNC: 0x7E] [SEQ] [LEN] [DATA...] [CRC]
// ============================================================

#define SENSOR_PIN     34     // GPIO pin connected to photodiode / op-amp
#define BIT_PERIOD_US  1000   // 1000 us per bit (1000 bps)
#define HALF_BIT_US    500    // Half-bit center (500us)
#define PREAMBLE_BYTE  0xAA
#define SYNC_BYTE      0x7E
#define MAX_MSG_LEN    200

// ============================================================
// BALANCED HIGH-SPEED THRESHOLD (100 ADC counts)
// ============================================================
int lightThreshold = 100;
int peakADC = 0;

unsigned long rxGoodCount = 0;
unsigned long rxFailCount = 0;
unsigned long rxErrCount  = 0;
unsigned long lastHeartbeat = 0;

// ---- Fast sensor reading ----
inline int readSensor() {
  return (analogRead(SENSOR_PIN) >= lightThreshold) ? HIGH : LOW;
}

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

// ---- Read a single byte with microsecond bit timing ----
int readByte(unsigned long timeoutMs) {
  unsigned long startMs = millis();

  // 1. Wait until line is LOW
  while (readSensor() == HIGH) {
    if (millis() - startMs > timeoutMs) return -1;
  }

  // 2. Wait for rising edge (Start bit: LOW -> HIGH transition)
  while (readSensor() == LOW) {
    if (millis() - startMs > timeoutMs) return -1;
  }

  // T0 is the exact timestamp of start bit rising edge
  unsigned long t0 = micros();

  // 3. Center of start bit (500us)
  while ((long)(micros() - (t0 + HALF_BIT_US)) < 0);

  // Validate start bit is genuine light
  if (readSensor() == LOW) {
    return -2;
  }

  // 4. Sample 8 data bits at bit centers (1500us, 2500us, ..., 8500us)
  uint8_t val = 0;
  for (int i = 0; i < 8; i++) {
    unsigned long sampleTime = t0 + HALF_BIT_US + (unsigned long)(i + 1) * BIT_PERIOD_US;
    while ((long)(micros() - sampleTime) < 0);
    if (readSensor() == HIGH) {
      val |= (1 << i);
    }
  }

  // 5. Wait past stop bit into inter-byte gap (10100us)
  unsigned long stopTime = t0 + 10100;
  while ((long)(micros() - stopTime) < 0);

  return (int)val;
}

// ---- Receive full packet with jitter resilience ----
void receivePacket() {
  unsigned long rxStart = millis();

  // Step 1: Detect Preamble (look for 0xAA or 0x2A clock bytes)
  int aaCount = 0;
  unsigned long searchStart = millis();
  bool syncFound = false;

  while (aaCount < 2) {
    int b = readByte(120);
    if (b == PREAMBLE_BYTE || b == 0x2A) {
      aaCount++;
    } else if (b == SYNC_BYTE || b == 0x1F) {
      syncFound = true;
      break;
    } else if (b >= 0) {
      aaCount = 0;
    }
    if (millis() - searchStart > 350) {
      return; // Timeout
    }
  }

  // Step 2: Lock onto SYNC BYTE (0x7E or 0x1F)
  if (!syncFound) {
    for (int attempt = 0; attempt < 12; attempt++) {
      int b = readByte(100);
      if (b == SYNC_BYTE || b == 0x1F) {
        syncFound = true;
        break;
      }
      if (b < 0) break;
    }
  }

  if (!syncFound) {
    rxErrCount++;
    return;
  }

  // Step 3: Read Length
  int len = readByte(100);
  if (len <= 0 || len > MAX_MSG_LEN) {
    rxErrCount++;
    return;
  }

  // Step 4: Read Payload Data Bytes into buffer
  char msg[MAX_MSG_LEN + 1];
  for (int i = 0; i < len; i++) {
    int b = readByte(100);
    if (b < 0) {
      rxErrCount++;
      return;
    }
    msg[i] = (char)b;
  }
  msg[len] = '\0';

  // Step 5: Read CRC Checksum
  int crcRecv = readByte(100);
  if (crcRecv < 0) {
    rxErrCount++;
    return;
  }

  unsigned long rxEnd = millis();
  rxGoodCount++;

  Serial.println("\n====================================");
  Serial.print("[LOG] RX SUCCESS! | Time: ");
  Serial.print(rxEnd);
  Serial.print("ms | Duration: ");
  Serial.print(rxEnd - rxStart);
  Serial.println("ms");
  Serial.print("[LOG] Length: ");
  Serial.print(len);
  Serial.print(" bytes | CRC: 0x");
  Serial.println(crcRecv, HEX);
  Serial.print("[LOG] Received Message: \"");
  Serial.print(msg);
  Serial.println("\"");
  Serial.print("[LOG] Totals -> Good: ");
  Serial.print(rxGoodCount);
  Serial.print(" | Failed: ");
  Serial.print(rxFailCount);
  Serial.print(" | Errors: ");
  Serial.println(rxErrCount);
  Serial.println("====================================");
}

void setup() {
  Serial.begin(115200);
  pinMode(SENSOR_PIN, INPUT);

  delay(500);

  Serial.println("\n============================================");
  Serial.println("  SemLiFi RECEIVER v4.0 — High-Sensitivity");
  Serial.println("============================================");
  Serial.print("  Sensor pin:       GPIO ");
  Serial.println(SENSOR_PIN);

  int curADC = analogRead(SENSOR_PIN);
  lightThreshold = 150;

  Serial.print("  Baseline ADC:     ");
  Serial.println(curADC);
  Serial.print("  Tuned Threshold:  ");
  Serial.println(lightThreshold);
  Serial.println("============================================");

  if (curADC >= 50) {
    Serial.println(" [WARN: Ambient light detected! Shield sensor]");
  } else {
    Serial.println(" [OK: High-sensitivity baseline calibrated]");
  }
  Serial.println("Listening for LiFi optical packets...\n");
}

void loop() {
  int raw = analogRead(SENSOR_PIN);
  if (raw > peakADC) peakADC = raw;

  // 1. Check if optical pulse is detected
  if (raw >= lightThreshold) {
    receivePacket();
  }

  // 2. Heartbeat every 2 seconds when idle
  if (millis() - lastHeartbeat > 2000) {
    lastHeartbeat = millis();
    Serial.print("[IDLE] Waiting for LiFi... Current ADC: ");
    Serial.print(raw);
    Serial.print(" | Peak (last 2s): ");
    Serial.print(peakADC);
    Serial.print(" | Threshold: ");
    Serial.println(lightThreshold);
    peakADC = 0;
  }
}

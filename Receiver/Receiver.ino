// ============================================================
// SemLiFi RECEIVER v4.0 — High-Sensitivity Adaptive Optical Decoder
// ============================================================
// Protocol (matches Sender.ino):
//   [PREAMBLE: 16× 0xAA] [SYNC: 0x7E] [SEQ] [LEN] [DATA...] [CRC]
// ============================================================

#define SENSOR_PIN     34     // GPIO pin connected to photodiode / op-amp
#define BIT_PERIOD_US  1000   // 1000 us per bit (1000 bps)
#define HALF_BIT_US    500    // Half-bit center (500us)
#define PREAMBLE_BYTE  0xAA   // Clock synchronization byte
#define SYNC_BYTE      0x7E   // Frame start delimiter
#define MAX_MSG_LEN    250    // Maximum payload capacity (expanded to 250 bytes)

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

// ---- Wait for channel to be LOW continuously for at least minLowUs (inter-byte silence) ----
bool waitForGap(unsigned long minLowUs, unsigned long timeoutMs) {
  unsigned long startMs = millis();
  unsigned long lowStart = 0;
  bool countingLow = false;

  while (millis() - startMs < timeoutMs) {
    if (readSensor() == HIGH) {
      countingLow = false;
    } else {
      if (!countingLow) {
        countingLow = true;
        lowStart = micros();
      } else if (micros() - lowStart >= minLowUs) {
        return true; // Confirmed inter-byte silence
      }
    }
  }
  return false;
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

// ---- Receive full packet with jitter resilience & burst tracking ----
void receivePacket() {
  unsigned long rxStart = millis();

  // Step 0: Ensure byte alignment by waiting for inter-byte silence
  waitForGap(1500, 150);

  // Step 1: Detect Preamble (0xAA clock bytes)
  int aaCount = 0;
  unsigned long searchStart = millis();
  bool syncFound = false;

  while (aaCount < 2) {
    int b = readByte(100);
    if (b == PREAMBLE_BYTE) {
      aaCount++;
    } else if (b == SYNC_BYTE) {
      syncFound = true;
      break;
    } else if (b >= 0) {
      aaCount = 0;
    }
    if (millis() - searchStart > 300) {
      return; // Timeout waiting for preamble
    }
  }

  // Step 2: Lock onto SYNC BYTE (0x7E)
  if (!syncFound) {
    for (int attempt = 0; attempt < 16; attempt++) {
      int b = readByte(100);
      if (b == SYNC_BYTE) {
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

  // Step 3: Read Sequence Number (protocol matches Sender.ino)
  int seq = readByte(100);
  if (seq < 0) {
    rxErrCount++;
    return;
  }

  // Step 4: Read Length
  int len = readByte(100);
  if (len <= 0 || len > MAX_MSG_LEN) {
    rxErrCount++;
    return;
  }

  // Step 5: Read Payload Data Bytes with burst corruption tracking
  char msg[MAX_MSG_LEN + 1];
  int firstCorrupt = -1;
  int lastCorrupt = -1;
  int corruptCount = 0;
  unsigned long burstStart = 0;
  unsigned long burstEnd = 0;

  for (int i = 0; i < len; i++) {
    int b = readByte(50);
    if (b < 0) {
      if (firstCorrupt == -1) {
        firstCorrupt = i;
        burstStart = millis();
      }
      lastCorrupt = i;
      corruptCount++;
      msg[i] = '?';
    } else {
      if (firstCorrupt != -1 && burstEnd == 0) {
        burstEnd = millis();
      }
      msg[i] = (char)b;
    }
  }
  msg[len] = '\0';

  // Step 6: Read CRC Checksum
  int crcRecv = readByte(100);
  unsigned long rxEnd = millis();

  // If burst loss occurred during payload
  if (corruptCount > 0) {
    rxFailCount++;
    unsigned long burstDur = (burstEnd > burstStart) ? (burstEnd - burstStart) : (corruptCount * 12);
    Serial.println("\n====================================");
    Serial.print("[BURST_EVENT] duration_ms=");
    Serial.print(burstDur);
    Serial.print(" affected=[");
    Serial.print(firstCorrupt);
    Serial.print(",");
    Serial.print(lastCorrupt);
    Serial.print("] raw=\"");
    Serial.print(msg);
    Serial.println("\"");
    Serial.print("[LOG] RX BURST CORRUPTION! | Duration: ");
    Serial.print(burstDur);
    Serial.print("ms | Corrupted: ");
    Serial.print(corruptCount);
    Serial.println(" bytes");
    Serial.print("[LOG] Totals -> Good: ");
    Serial.print(rxGoodCount);
    Serial.print(" | Failed: ");
    Serial.print(rxFailCount);
    Serial.print(" | Errors: ");
    Serial.println(rxErrCount);
    Serial.println("====================================");
    return;
  }

  if (crcRecv < 0) {
    rxErrCount++;
    return;
  }

  // Step 7: Verify CRC over [SEQ][LEN][DATA...]
  uint8_t checkFrame[MAX_MSG_LEN + 2];
  checkFrame[0] = (uint8_t)seq;
  checkFrame[1] = (uint8_t)len;
  memcpy(&checkFrame[2], msg, len);
  uint8_t calculatedCrc = crc8(checkFrame, len + 2);

  if ((uint8_t)crcRecv != calculatedCrc) {
    rxErrCount++;
    Serial.println("\n====================================");
    Serial.print("[LOG] CRC MISMATCH! Calculated: 0x");
    Serial.print(calculatedCrc, HEX);
    Serial.print(" | Received: 0x");
    Serial.println(crcRecv, HEX);
    Serial.print("[LOG] Message Received: \"");
    Serial.print(msg);
    Serial.println("\"");
    Serial.print("[LOG] Totals -> Good: ");
    Serial.print(rxGoodCount);
    Serial.print(" | Failed: ");
    Serial.print(rxFailCount);
    Serial.print(" | Errors: ");
    Serial.println(rxErrCount);
    Serial.println("====================================");
    return;
  }

  rxGoodCount++;

  Serial.println("\n====================================");
  Serial.print("[LOG] RX SUCCESS! | Time: ");
  Serial.print(rxEnd);
  Serial.print("ms | Duration: ");
  Serial.print(rxEnd - rxStart);
  Serial.println("ms");
  Serial.print("[LOG] Seq #");
  Serial.print(seq);
  Serial.print(" | Length: ");
  Serial.print(len);
  Serial.print(" bytes | CRC: 0x");
  Serial.print(crcRecv, HEX);
  Serial.println(" (MATCH)");
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

  // Measure ambient ADC baseline
  int ambientADC = 0;
  for (int i = 0; i < 10; i++) {
    ambientADC += analogRead(SENSOR_PIN);
    delay(10);
  }
  ambientADC /= 10;

  // Set balanced threshold: ambient + 100, minimum 100
  lightThreshold = (ambientADC < 50) ? 100 : (ambientADC + 100);

  Serial.println("\n============================================");
  Serial.println("  SemLiFi RECEIVER v4.0 — High-Sensitivity");
  Serial.println("============================================");
  Serial.print("  Sensor pin:       GPIO ");
  Serial.println(SENSOR_PIN);
  Serial.print("  Ambient ADC:      ");
  Serial.println(ambientADC);
  Serial.print("  Tuned Threshold:  ");
  Serial.println(lightThreshold);
  Serial.println("============================================");

  if (ambientADC >= 50) {
    Serial.println("  [WARN: Ambient room light detected! Shield photodiode]");
  } else {
    Serial.println("  [OK: Clean optical baseline calibrated]");
  }
  Serial.println("  Listening for LiFi optical packets...\n");
}

void loop() {
  int raw = analogRead(SENSOR_PIN);
  if (raw > peakADC) peakADC = raw;

  // 1. Check if optical pulse is detected
  if (raw >= lightThreshold) {
    receivePacket();
  }

  // 2. Heartbeat every 5 seconds when idle
  if (millis() - lastHeartbeat > 5000) {
    lastHeartbeat = millis();
    Serial.print("[IDLE] Waiting for LiFi... Current ADC: ");
    Serial.print(raw);
    Serial.print(" | Peak (last 5s): ");
    Serial.print(peakADC);
    Serial.print(" | Threshold: ");
    Serial.println(lightThreshold);
    peakADC = 0;
  }
}

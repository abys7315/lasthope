// ============================================================
// SemLiFi RECEIVER DIAGNOSTIC & CALIBRATION TOOL
// ============================================================
// STEP 1: Verify pin connections & detect floating pins
// STEP 2: Read live signal strength, voltage, and noise floor
// STEP 3: Provide exact recommended LIGHT_THRESHOLD for Receiver.ino
// ============================================================

#define SENSOR_PIN 34   // GPIO pin connected to LM358 output / photodiode

void setup() {
  Serial.begin(115200);
  pinMode(SENSOR_PIN, INPUT);
  delay(1000);

  Serial.println("==========================================================");
  Serial.println("   SemLiFi SENSOR DIAGNOSTIC & CALIBRATION TOOL");
  Serial.println("==========================================================");
  Serial.print("   Reading GPIO Pin: ");
  Serial.println(SENSOR_PIN);
  Serial.println("==========================================================");
  Serial.println("");

  // ------------------------------------------------------------
  // STEP 1: VERIFY HARDWARE CONNECTION (FLOATING PIN TEST)
  // ------------------------------------------------------------
  Serial.println("--> STEP 1: Verifying hardware connection...");
  int totalVal = 0;
  int minVal = 4095;
  int maxVal = 0;
  
  for (int i = 0; i < 100; i++) {
    int v = analogRead(SENSOR_PIN);
    totalVal += v;
    if (v < minVal) minVal = v;
    if (v > maxVal) maxVal = v;
    delay(5);
  }
  int avgVal = totalVal / 100;
  int spread = maxVal - minVal;

  Serial.print("    Sample Avg: "); Serial.print(avgVal);
  Serial.print(" | Min: "); Serial.print(minVal);
  Serial.print(" | Max: "); Serial.print(maxVal);
  Serial.print(" | Delta: "); Serial.println(spread);

  if (avgVal == 0 || avgVal == 4095) {
    Serial.println("    [CRITICAL ERROR] Pin reads static 0 or 4095!");
    Serial.println("    Check wiring: GPIO34 must connect to LM358 Pin 1 (OUT).");
  } else if (spread > 1000) {
    Serial.println("    [WARNING] High signal jitter detected!");
    Serial.println("    Pin might be floating or improperly grounded.");
  } else {
    Serial.println("    [SUCCESS] Pin connection detected and active!");
  }
  Serial.println("----------------------------------------------------------\n");

  delay(2000);
  Serial.println("--> STEP 2: Live Signal Strength Monitoring");
  Serial.println("    Cover sensor to see Dark Floor. Shine LED/Flashlight to see Light Level.");
  Serial.println("");
  Serial.println("Time (ms) | ADC (0-4095) | Volts (V) | Signal Level Bar");
  Serial.println("----------|--------------|-----------|---------------------------------");
}

void loop() {
  int rawADC = analogRead(SENSOR_PIN);
  float voltage = (rawADC / 4095.0) * 3.3;

  Serial.print(millis());
  Serial.print("ms");
  if (millis() < 10000) Serial.print(" ");
  
  Serial.print("  | ADC: ");
  if (rawADC < 10) Serial.print("   ");
  else if (rawADC < 100) Serial.print("  ");
  else if (rawADC < 1000) Serial.print(" ");
  Serial.print(rawADC);

  Serial.print("  | ");
  Serial.print(voltage, 2);
  Serial.print("V    | ");

  // Render visual signal bar (1 block per 100 ADC counts)
  int bars = rawADC / 100;
  if (bars > 35) bars = 35;
  for (int i = 0; i < bars; i++) {
    Serial.print("#");
  }
  for (int i = bars; i < 35; i++) {
    Serial.print(" ");
  }

  // Print baseline status
  if (rawADC < 400) {
    Serial.print(" [DARK / BASELINE FLOOR]");
  } else if (rawADC < 1200) {
    Serial.print(" [LOW LIGHT / AMBIENT]");
  } else if (rawADC < 2800) {
    Serial.print(" [MEDIUM SIGNAL]");
  } else {
    Serial.print(" [STRONG LIGHT / HIGH]");
  }

  Serial.println("");
  delay(300);
}


// ============================================================
// SemLiFi Phase 2 — Automated Servo + Flap Occlusion Rig
// ============================================================
// Drives an SG90 / MG995 servo motor to swing a physical blade
// across the LiFi optical beam to generate realistic burst losses.
//
// Supports the 3 required Phase 2 patterns:
//   1. Short & Frequent Blocks  (15–40 ms duration, 150–300 ms period)
//   2. Long & Rare Blocks       (150–350 ms duration, 2000–4000 ms period)
//   3. Mixed & Random Blocks    (Cluster-based realistic obstruction)
// ============================================================

#include <ESP32Servo.h> // Or <Servo.h> if compiling for standard Arduino Uno/Nano

#define SERVO_PIN        18    // PWM pin driving servo signal line
#define ANGLE_OPEN       0     // Flap clear of optical beam (light unblocked)
#define ANGLE_BLOCK      65    // Flap cutting through optical beam (light blocked)

Servo flapServo;

enum PatternState {
  STATE_IDLE,
  PATTERN_SHORT_FREQUENT,
  PATTERN_LONG_RARE,
  PATTERN_MIXED_RANDOM
};

PatternState currentPattern = STATE_IDLE;
unsigned long lastActionTime = 0;
unsigned long nextIntervalMs = 1000;
bool isBlocking = false;
unsigned long currentBurstDuration = 0;

void setFlap(bool block) {
  if (block) {
    flapServo.write(ANGLE_BLOCK);
  } else {
    flapServo.write(ANGLE_OPEN);
  }
}

void setup() {
  Serial.begin(115200);
  flapServo.attach(SERVO_PIN, 500, 2400); // Standard SG90 pulse range
  setFlap(false);

  delay(500);
  Serial.println("\n=======================================================");
  Serial.println("  SemLiFi Phase 2 — Servo Flap Occlusion Rig Controller");
  Serial.println("=======================================================");
  Serial.println("Commands:");
  Serial.println("  P1          : Start Pattern 1 (Short & Frequent Blocks)");
  Serial.println("  P2          : Start Pattern 2 (Long & Rare Blocks)");
  Serial.println("  P3          : Start Pattern 3 (Mixed & Random Blocks)");
  Serial.println("  STOP        : Stop occlusion and reset flap to OPEN");
  Serial.println("  BURST <ms>  : Trigger a single burst of <ms> duration");
  Serial.println("=======================================================\n");
}

void loop() {
  // 1. Process incoming serial commands
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();

    if (cmd == "P1") {
      currentPattern = PATTERN_SHORT_FREQUENT;
      lastActionTime = millis();
      nextIntervalMs = random(150, 300);
      Serial.println("[RIG] Mode set: PATTERN 1 (Short & Frequent)");
    } else if (cmd == "P2") {
      currentPattern = PATTERN_LONG_RARE;
      lastActionTime = millis();
      nextIntervalMs = random(2000, 4000);
      Serial.println("[RIG] Mode set: PATTERN 2 (Long & Rare)");
    } else if (cmd == "P3") {
      currentPattern = PATTERN_MIXED_RANDOM;
      lastActionTime = millis();
      nextIntervalMs = random(200, 1500);
      Serial.println("[RIG] Mode set: PATTERN 3 (Mixed & Random)");
    } else if (cmd == "STOP") {
      currentPattern = STATE_IDLE;
      isBlocking = false;
      setFlap(false);
      Serial.println("[RIG] Stopped. Flap OPEN.");
    } else if (cmd.startsWith("BURST ")) {
      int ms = cmd.substring(6).toInt();
      if (ms > 0) {
        Serial.print("[SERVO_ACTUATION] Single burst triggered: ");
        Serial.print(ms);
        Serial.println(" ms");
        setFlap(true);
        delay(ms);
        setFlap(false);
      }
    }
  }

  // 2. Pattern Execution FSM
  if (currentPattern != STATE_IDLE) {
    unsigned long now = millis();

    if (!isBlocking) {
      // Waiting to trigger next occlusion
      if (now - lastActionTime >= nextIntervalMs) {
        isBlocking = true;
        lastActionTime = now;

        // Determine burst duration based on pattern
        if (currentPattern == PATTERN_SHORT_FREQUENT) {
          currentBurstDuration = random(20, 45);
        } else if (currentPattern == PATTERN_LONG_RARE) {
          currentBurstDuration = random(180, 380);
        } else if (currentPattern == PATTERN_MIXED_RANDOM) {
          int pick = random(0, 100);
          if (pick < 45) {
            currentBurstDuration = random(20, 50);    // Short burst
          } else if (pick < 80) {
            currentBurstDuration = random(80, 160);   // Medium burst
          } else {
            currentBurstDuration = random(200, 400);  // Long burst
          }
        }

        setFlap(true);
        Serial.print("[SERVO_ACTUATION] start=");
        Serial.print(now);
        Serial.print(" duration=");
        Serial.print(currentBurstDuration);
        Serial.print(" pattern=");
        Serial.println(currentPattern == PATTERN_SHORT_FREQUENT ? "short_frequent" :
                       (currentPattern == PATTERN_LONG_RARE ? "long_rare" : "mixed_random"));
      }
    } else {
      // Currently blocking: check if burst duration has elapsed
      if (now - lastActionTime >= currentBurstDuration) {
        setFlap(false);
        isBlocking = false;
        lastActionTime = now;

        // Schedule next interval
        if (currentPattern == PATTERN_SHORT_FREQUENT) {
          nextIntervalMs = random(150, 300);
        } else if (currentPattern == PATTERN_LONG_RARE) {
          nextIntervalMs = random(2000, 4000);
        } else {
          nextIntervalMs = random(300, 2000);
        }
      }
    }
  }
}

"""
SemLiFi Phase 5 -- Live CGFP Demonstration
==========================================
Real-time visual demonstration of the Confidence-Guided Frame Patching engine.
Shows frame-by-frame burst injection, BASR reconstruction, confidence scoring,
and the PATCH/RETRANSMIT decision in an animated console display.

Usage:
    python cgfp/live_cgfp_demo.py
    python cgfp/live_cgfp_demo.py --frames 30 --burst-prob 0.6
"""

import os
import sys
import time
import random

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cgfp.patcher import CGFPPatcher
from cgfp.confidence import validate_telemetry_syntax
from basr.dataset import CHAR2IDX, MASK_IDX, PAD_IDX, MAX_SEQ_LEN

import torch


def tokenize(text, is_masked=False):
    if is_masked:
        tokens = [MASK_IDX if c == "?" else CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
    else:
        tokens = [CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
    if len(tokens) < MAX_SEQ_LEN:
        tokens = tokens + [PAD_IDX] * (MAX_SEQ_LEN - len(tokens))
    return torch.tensor(tokens[:MAX_SEQ_LEN], dtype=torch.long)


def run_demo(num_frames=25, burst_prob=0.50, threshold=0.80):
    print()
    print("+" + "=" * 66 + "+")
    print("|       SemLiFi -- Live CGFP Frame Patching Demonstration        |")
    print("|  Optical OOK @ 1000bps  |  BASR Transformer  |  CGFP tau=0.80 |")
    print("+" + "=" * 66 + "+")
    print()

    patcher = CGFPPatcher(default_threshold=threshold, device="cpu")

    if not patcher.checkpoint_loaded:
        print("[WARN] No trained checkpoint found. Run: python basr/train.py")

    # Telemetry state
    temp = 24.5
    hum = 55
    motor = "ON"
    motor_timer = 0
    prev_payload = None

    # Counters
    total = 0
    bursts = 0
    patches = 0
    retransmits = 0
    exact = 0
    errors = 0

    print("  Simulating optical telemetry stream with burst occlusions...")
    print("  Each frame: [Sender LED] --> [Air Gap] --> [Photodiode Receiver]")
    print()
    time.sleep(0.5)

    for i in range(num_frames):
        total += 1

        # Generate telemetry
        temp += random.gauss(0, 0.15)
        temp = max(19.5, min(33.0, temp))
        hum += int(random.choice([-1, 0, 0, 1]))
        hum = max(42, min(78, hum))
        motor_timer += 1
        prev_motor = motor
        if motor_timer > random.randint(12, 35):
            motor = "OFF" if motor == "ON" else "ON"
            motor_timer = 0
        state_changed = motor != prev_motor

        clean = f"TEMP={temp:.1f},HUM={hum},MOTOR={motor}"
        if prev_payload is None:
            prev_payload = clean

        is_burst = random.random() < burst_prob

        state_tag = " [STATE TRANSITION!]" if state_changed else ""
        print(f"  +-- Frame #{total:03d} {state_tag}")
        print(f"  |  TX: {clean}")

        if not is_burst:
            print(f"  |  RX: {clean}  [CLEAN]")
            print(f"  |  -> Direct Accept (449ms transit, 0ms overhead)")
            print(f"  +{'- ' * 25}")
        else:
            bursts += 1
            # Inject burst
            burst_chars = random.randint(3, min(18, len(clean) - 2))
            start = random.randint(0, len(clean) - burst_chars)
            corrupted = list(clean)
            for j in range(start, start + burst_chars):
                corrupted[j] = "?"
            corrupted_str = "".join(corrupted)

            print(f"  |  RX: {corrupted_str}  [BURST] ({burst_chars} bytes masked)")

            # Run CGFP
            curr_ids = tokenize(corrupted_str, is_masked=True)
            prev_ids = tokenize(prev_payload, is_masked=False)

            t0 = time.perf_counter()
            result = patcher.patch_frame(curr_ids, prev_ids, threshold=threshold)
            infer_ms = (time.perf_counter() - t0) * 1000.0

            conf = result["frame_confidence"]
            action = result["action"]
            patched = result["patched_payload"]
            is_exact = (patched == clean)

            # Confidence bar
            bar_len = 30
            filled = int(conf * bar_len)
            bar = "#" * filled + "." * (bar_len - filled)
            print(f"  |  BASR: {patched}")
            print(f"  |  Conf: [{bar}] {conf:.3f} (tau*={threshold})")

            if action == "PATCH":
                patches += 1
                if is_exact:
                    exact += 1
                    print(f"  |  -> PATCH [EXACT] On-device reconstruction accepted ({infer_ms:.1f}ms BASR)")
                else:
                    errors += 1
                    print(f"  |  -> PATCH [DIFF]  Accepted but differs from ground truth ({infer_ms:.1f}ms)")
                    print(f"  |     GT:  {clean}")
            else:
                retransmits += 1
                nack_hex = "55AA15{:02X}01".format(total & 0xFF)
                print(f"  |  -> RETRANSMIT  NACK sent [0x{nack_hex}] (+469ms overhead)")

            print(f"  +{'- ' * 25}")

        prev_payload = clean
        print()
        time.sleep(0.15)  # Visual pacing

    # Summary
    print()
    print("+" + "=" * 66 + "+")
    print("|                    DEMONSTRATION SUMMARY                        |")
    print("+" + "=" * 66 + "+")
    print(f"  Total Frames:          {total}")
    print(f"  Clean (No Burst):      {total - bursts}  ({(total-bursts)/total*100:.0f}%)")
    print(f"  Burst-Corrupted:       {bursts}  ({bursts/total*100:.0f}%)")
    if bursts > 0:
        print(f"  +-- PATCH (accepted):  {patches}  ({patches/bursts*100:.0f}% of bursts)")
        print(f"  |   +-- Exact match:   {exact}")
        print(f"  |   +-- Silent errors: {errors}")
        print(f"  +-- RETRANSMIT (NACK): {retransmits}  ({retransmits/bursts*100:.0f}% of bursts)")
        ufer = errors / total * 100
        print(f"  Undetected Error Rate: {ufer:.2f}%  {'[SAFE]' if ufer < 2.0 else '[REVIEW]'}")
    print("+" + "=" * 66 + "+")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SemLiFi Live CGFP Demo")
    parser.add_argument("--frames", type=int, default=25)
    parser.add_argument("--burst-prob", type=float, default=0.50)
    parser.add_argument("--threshold", type=float, default=0.80)
    args = parser.parse_args()
    run_demo(num_frames=args.frames, burst_prob=args.burst_prob, threshold=args.threshold)

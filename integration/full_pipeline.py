"""
SemLiFi Phase 5 — Full System Integration Pipeline
===================================================
End-to-end integration of the complete SemLiFi optical communication system:

    [ESP32 Sender] --OOK LED--> [Air Gap] --Photodiode--> [ESP32 Receiver]
         |                                                       |
         |                    (Burst Occlusion)                  |
         |                                                       v
         |                                              [Python Host]
         |                                                       |
         |              +------- BASR Transformer <--------------+
         |              |         (74k params, 3ms)
         |              v
         |        CGFP Confidence Engine
         |        (token prob × burst penalty × syntax)
         |              |
         |       [C >= τ*?]---YES---> PATCH (0ms overhead)
         |              |
         |             NO
         |              |
         +<--- NACK ----+  (Selective Back-Channel)

Pipeline modes:
  1. LIVE:     Connects to real ESP32 hardware (COM11 Receiver, COM12 Sender)
  2. SIMULATE: Runs full pipeline on synthetic burst-injected telemetry streams

Usage:
  python integration/full_pipeline.py --mode simulate --frames 50
  python integration/full_pipeline.py --mode live --sender COM12 --receiver COM11
"""

import os
import sys
import time
import json
import random
import argparse
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cgfp.patcher import CGFPPatcher
from cgfp.backchannel import SelectiveBackChannel, BackChannelPacket
from cgfp.confidence import validate_telemetry_syntax
from basr.dataset import CHAR2IDX, IDX2CHAR, MASK_IDX, PAD_IDX, MAX_SEQ_LEN

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ── Physical channel constants (Phase 1 measured) ──────────────────────
CLEAN_TRANSIT_MS = 449.0
NACK_OVERHEAD_MS = 20.0
ARQ_PENALTY_MS = CLEAN_TRANSIT_MS + NACK_OVERHEAD_MS

# ── Burst injection parameters (Phase 2 empirical) ────────────────────
BURST_PROB_PER_FRAME = 0.40  # 40% of frames experience occlusion
MIN_BURST_CHARS = 3
MAX_BURST_CHARS = 20


class SemLiFiPipeline:
    """
    Full SemLiFi system integrating:
      - Optical frame reception (real or simulated)
      - BASR Transformer burst reconstruction
      - CGFP confidence-gated frame patching
      - Selective back-channel NACK protocol
    """

    def __init__(self, threshold=0.80, device="cpu"):
        print("=" * 72)
        print("       SEMLIFI FULL SYSTEM INTEGRATION — PHASE 5 PIPELINE")
        print("=" * 72)

        # Initialize CGFP Patcher (loads BASR checkpoint)
        print("[INIT] Loading BASR Transformer + CGFP Engine...")
        self.patcher = CGFPPatcher(default_threshold=threshold, device=device)
        self.backchannel = SelectiveBackChannel()
        self.threshold = threshold

        # Telemetry state for simulation
        self.temp = 24.5
        self.hum = 55
        self.motor = "ON"
        self.motor_timer = 0
        self.frame_counter = 0

        # Performance counters
        self.stats = {
            "total_frames": 0,
            "clean_frames": 0,
            "burst_frames": 0,
            "patched_frames": 0,
            "retransmit_frames": 0,
            "exact_patches": 0,
            "incorrect_patches": 0,
            "total_latency_ms": 0.0,
            "basr_inference_ms": 0.0,
            "state_transitions_total": 0,
            "state_transitions_correct": 0,
        }

        if self.patcher.checkpoint_loaded:
            print(f"[INIT] BASR checkpoint loaded successfully.")
        else:
            print(f"[WARN] BASR checkpoint not found -- model uses random weights.")

        print(f"[INIT] CGFP threshold tau* = {threshold}")
        print(f"[INIT] Pipeline ready.\n")

    def _generate_telemetry(self):
        """Generates the next frame in a physically correlated telemetry stream."""
        self.temp += random.gauss(0, 0.15)
        self.temp = max(19.5, min(33.0, self.temp))

        self.hum += int(random.choice([-1, 0, 0, 1]))
        self.hum = max(42, min(78, self.hum))

        self.motor_timer += 1
        prev_motor = self.motor
        if self.motor_timer > random.randint(12, 35):
            self.motor = "OFF" if self.motor == "ON" else "ON"
            self.motor_timer = 0

        text = f"TEMP={self.temp:.1f},HUM={self.hum},MOTOR={self.motor}"
        state_changed = (self.motor != prev_motor)
        self.frame_counter += 1
        return text, state_changed

    def _inject_burst(self, clean_payload):
        """Simulates a physical optical burst occlusion on a clean frame."""
        burst_chars = random.randint(MIN_BURST_CHARS, min(MAX_BURST_CHARS, len(clean_payload) - 2))
        start_idx = random.randint(0, len(clean_payload) - burst_chars)
        end_idx = start_idx + burst_chars

        corrupted = list(clean_payload)
        for i in range(start_idx, end_idx):
            corrupted[i] = "?"
        return "".join(corrupted), (start_idx, end_idx)

    def _tokenize(self, text, is_masked=False):
        """Convert text to padded token tensor."""
        import torch
        if is_masked:
            tokens = [MASK_IDX if c == "?" else CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
        else:
            tokens = [CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
        if len(tokens) < MAX_SEQ_LEN:
            tokens = tokens + [PAD_IDX] * (MAX_SEQ_LEN - len(tokens))
        return torch.tensor(tokens[:MAX_SEQ_LEN], dtype=torch.long)

    def process_frame(self, clean_payload, prev_payload, is_burst, corrupted_payload=None, burst_range=None, state_changed=False):
        """
        Process a single frame through the full SemLiFi pipeline.
        Returns a detailed result dict.
        """
        self.stats["total_frames"] += 1
        frame_id = self.stats["total_frames"]

        if state_changed:
            self.stats["state_transitions_total"] += 1

        if not is_burst:
            # Clean frame — direct acceptance
            self.stats["clean_frames"] += 1
            self.stats["total_latency_ms"] += CLEAN_TRANSIT_MS
            return {
                "frame_id": frame_id,
                "status": "CLEAN",
                "action": "ACCEPT",
                "payload": clean_payload,
                "latency_ms": CLEAN_TRANSIT_MS,
                "confidence": 1.0,
                "is_exact": True,
                "state_changed": state_changed,
            }

        # Burst-corrupted frame — invoke CGFP pipeline
        self.stats["burst_frames"] += 1

        if corrupted_payload is None:
            corrupted_payload, burst_range = self._inject_burst(clean_payload)

        curr_ids = self._tokenize(corrupted_payload, is_masked=True)
        prev_ids = self._tokenize(prev_payload, is_masked=False)

        # Run BASR + CGFP
        t0 = time.perf_counter()
        result = self.patcher.patch_frame(curr_ids, prev_ids, threshold=self.threshold)
        inference_ms = (time.perf_counter() - t0) * 1000.0
        self.stats["basr_inference_ms"] += inference_ms

        action = result["action"]
        patched = result["patched_payload"]
        confidence = result["frame_confidence"]
        is_exact = (patched == clean_payload)

        if action == "PATCH":
            self.stats["patched_frames"] += 1
            self.stats["total_latency_ms"] += CLEAN_TRANSIT_MS
            if is_exact:
                self.stats["exact_patches"] += 1
            else:
                self.stats["incorrect_patches"] += 1

            if state_changed:
                # Check if the motor state was correctly reconstructed
                if f"MOTOR={clean_payload.split('MOTOR=')[1]}" in patched:
                    self.stats["state_transitions_correct"] += 1
        else:
            # RETRANSMIT — send NACK, accept retransmitted clean frame
            self.stats["retransmit_frames"] += 1
            self.stats["total_latency_ms"] += CLEAN_TRANSIT_MS + ARQ_PENALTY_MS
            self.backchannel.send_nack(frame_id, confidence)
            self.backchannel.total_frames_evaluated += 1
            patched = clean_payload  # Retransmission delivers clean frame
            is_exact = True

            if state_changed:
                self.stats["state_transitions_correct"] += 1  # Retransmission is always correct

        return {
            "frame_id": frame_id,
            "status": "BURST",
            "action": action,
            "clean_payload": clean_payload,
            "corrupted_payload": corrupted_payload,
            "patched_payload": patched,
            "confidence": confidence,
            "is_exact": is_exact,
            "burst_range": burst_range,
            "inference_ms": round(inference_ms, 2),
            "latency_ms": CLEAN_TRANSIT_MS if action == "PATCH" else CLEAN_TRANSIT_MS + ARQ_PENALTY_MS,
            "state_changed": state_changed,
            "confidence_details": result.get("confidence_details", {}),
        }

    def run_simulation(self, num_frames=50, burst_probability=BURST_PROB_PER_FRAME):
        """
        Runs a full simulation of the SemLiFi pipeline on synthetic telemetry.
        """
        print("-" * 72)
        print(f"  SIMULATION MODE -- {num_frames} telemetry frames, burst_prob={burst_probability:.0%}")
        print("-" * 72)
        print(f"{'#':>4} {'Status':<7} {'Action':<11} {'Conf':>6} {'Lat(ms)':>8} {'Exact':>5}  Payload")
        print("-" * 72)

        prev_payload = None
        results = []

        for i in range(num_frames):
            clean, state_changed = self._generate_telemetry()

            if prev_payload is None:
                prev_payload = clean

            is_burst = random.random() < burst_probability

            if is_burst:
                corrupted, burst_range = self._inject_burst(clean)
            else:
                corrupted = None
                burst_range = None

            res = self.process_frame(
                clean_payload=clean,
                prev_payload=prev_payload,
                is_burst=is_burst,
                corrupted_payload=corrupted,
                burst_range=burst_range,
                state_changed=state_changed,
            )
            results.append(res)

            # Pretty-print each frame
            status_icon = "[OK]" if res["status"] == "CLEAN" else "[!!]"
            action_str = res["action"]
            conf_str = f"{res['confidence']:.3f}" if res["status"] == "BURST" else "  -  "
            exact_str = "Y" if res["is_exact"] else "N"
            lat_str = f"{res['latency_ms']:.0f}"
            payload_display = res.get("patched_payload", res.get("payload", ""))

            if res["status"] == "BURST" and res["action"] == "PATCH" and not res["is_exact"]:
                exact_str = "N ERR"

            state_tag = " [STATE TRANSITION]" if state_changed else ""

            print(f"{res['frame_id']:>4} {status_icon} {res['status']:<5} {action_str:<11} {conf_str:>6} {lat_str:>8} {exact_str:>5}  {payload_display}{state_tag}")

            prev_payload = clean  # Ground truth for next frame's reference

        self._print_summary(results)
        return results

    def run_live(self, sender_port="COM12", receiver_port="COM11", num_frames=20):
        """
        Runs the pipeline with real ESP32 hardware.
        Sends telemetry via sender, reads from receiver, applies CGFP.
        """
        try:
            import serial
        except ImportError:
            print("[ERROR] pyserial required. Run: pip install pyserial")
            return []

        print("-" * 72)
        print(f"  LIVE MODE -- Sender: {sender_port}, Receiver: {receiver_port}")
        print(f"  Transmitting {num_frames} frames over optical LiFi link...")
        print("-" * 72)

        try:
            tx = serial.Serial(sender_port, 115200, timeout=0.5)
            rx = serial.Serial(receiver_port, 115200, timeout=0.5)
            tx.dtr = True
            rx.dtr = True
            time.sleep(1.5)
            tx.reset_input_buffer()
            rx.reset_input_buffer()
        except Exception as e:
            print(f"[ERROR] Could not open serial ports: {e}")
            return []

        prev_payload = None
        results = []

        print(f"\n{'#':>4} {'TX Payload':<30} {'RX Status':<12} {'Action':<11} {'Conf':>6} {'Lat(ms)':>8}")
        print("-" * 72)

        for i in range(num_frames):
            clean, state_changed = self._generate_telemetry()
            if prev_payload is None:
                prev_payload = clean

            # Transmit over optical link
            tx.write((clean + "\n").encode("utf-8"))
            tx.flush()

            # Wait for receiver to decode
            t_send = time.time()
            rx_buffer = b""
            rx_decoded = None
            while time.time() - t_send < 4.0:
                if rx.in_waiting > 0:
                    rx_buffer += rx.read(rx.in_waiting)
                    decoded = rx_buffer.decode("utf-8", errors="ignore")
                    if "Received Message:" in decoded:
                        # Extract the received message
                        for line in decoded.split("\n"):
                            if "Received Message:" in line:
                                rx_decoded = line.split('"')[1] if '"' in line else line.split(":")[-1].strip()
                                break
                        break
                time.sleep(0.02)

            transit_ms = (time.time() - t_send) * 1000.0

            if rx_decoded is None:
                # Frame lost — treat as full burst
                res = self.process_frame(
                    clean_payload=clean,
                    prev_payload=prev_payload,
                    is_burst=True,
                    corrupted_payload="?" * len(clean),
                    burst_range=(0, len(clean)),
                    state_changed=state_changed,
                )
                rx_status = "LOST"
            elif rx_decoded == clean:
                # Clean frame received
                res = self.process_frame(
                    clean_payload=clean,
                    prev_payload=prev_payload,
                    is_burst=False,
                    state_changed=state_changed,
                )
                rx_status = "CLEAN"
            else:
                # Partial corruption — mark differences as masked
                corrupted = list(rx_decoded)
                for j in range(min(len(corrupted), len(clean))):
                    if j < len(corrupted) and corrupted[j] != clean[j]:
                        corrupted[j] = "?"
                corrupted_str = "".join(corrupted)
                res = self.process_frame(
                    clean_payload=clean,
                    prev_payload=prev_payload,
                    is_burst=True,
                    corrupted_payload=corrupted_str,
                    burst_range=(0, len(clean)),
                    state_changed=state_changed,
                )
                rx_status = "CORRUPT"

            results.append(res)
            conf_str = f"{res['confidence']:.3f}" if res.get('confidence') else "  —  "
            print(f"{i+1:>4} {clean:<30} {rx_status:<12} {res['action']:<11} {conf_str:>6} {transit_ms:>8.0f}")

            prev_payload = clean
            time.sleep(0.3)

        tx.close()
        rx.close()
        self._print_summary(results)
        return results

    def _print_summary(self, results):
        """Print comprehensive pipeline performance summary."""
        s = self.stats
        total = s["total_frames"]
        if total == 0:
            return

        burst = s["burst_frames"]
        patched = s["patched_frames"]
        retransmit = s["retransmit_frames"]
        exact = s["exact_patches"]
        incorrect = s["incorrect_patches"]
        avg_latency = s["total_latency_ms"] / total
        avg_inference = s["basr_inference_ms"] / max(1, burst)

        # Retransmission reduction: how many burst frames avoided retransmission
        retrans_reduction = (patched / max(1, burst)) * 100.0
        # Undetected error rate
        ufer = (incorrect / max(1, total)) * 100.0
        # State transition accuracy
        st_total = s["state_transitions_total"]
        st_correct = s["state_transitions_correct"]
        st_acc = (st_correct / max(1, st_total)) * 100.0

        # Latency comparison vs pure ARQ
        pure_arq_lat = CLEAN_TRANSIT_MS + (burst / max(1, total)) * ARQ_PENALTY_MS
        latency_savings = ((pure_arq_lat - avg_latency) / pure_arq_lat) * 100.0 if pure_arq_lat > 0 else 0

        print("\n" + "=" * 72)
        print("       SEMLIFI PHASE 5 -- FULL PIPELINE PERFORMANCE REPORT")
        print("=" * 72)
        print(f"")
        print(f"  +-- Channel Statistics -----------------------------------------------+")
        print(f"  |  Total Frames Processed:     {total:>6}                          |")
        print(f"  |  Clean Frames (No Burst):    {s['clean_frames']:>6}  ({s['clean_frames']/total*100:>5.1f}%)              |")
        print(f"  |  Burst-Corrupted Frames:     {burst:>6}  ({burst/total*100:>5.1f}%)              |")
        print(f"  +--------------------------------------------------------------------+")
        print(f"")
        print(f"  +-- CGFP Decision Engine (tau* = {self.threshold:.2f}) ----------------------------+")
        print(f"  |  PATCH (On-Device Fix):      {patched:>6}  ({patched/max(1,burst)*100:>5.1f}% of bursts)     |")
        print(f"  |    +-- Exact Patches:         {exact:>6}  ({exact/max(1,patched)*100:>5.1f}% patch accuracy) |")
        print(f"  |    +-- Incorrect Patches:     {incorrect:>6}  (Undetected errors)       |")
        print(f"  |  RETRANSMIT (NACK Sent):     {retransmit:>6}  ({retransmit/max(1,burst)*100:>5.1f}% of bursts)     |")
        print(f"  +--------------------------------------------------------------------+")
        print(f"")
        print(f"  +-- Key Performance Indicators ---------------------------------------+")
        print(f"  |  Retransmission Reduction:    {retrans_reduction:>5.1f}%                         |")
        print(f"  |  Undetected Frame Error Rate: {ufer:>5.2f}%  (Safety limit: <2.0%)   |")
        print(f"  |  Mean Delivery Latency:       {avg_latency:>6.1f} ms                      |")
        print(f"  |  Mean BASR Inference Time:    {avg_inference:>6.2f} ms                      |")
        print(f"  |  State Transition Accuracy:   {st_acc:>5.1f}%  ({st_correct}/{st_total})               |")
        print(f"  +--------------------------------------------------------------------+")
        print(f"")
        print(f"  +-- Protocol Comparison ----------------------------------------------+")
        print(f"  |  Pure ARQ Baseline Latency:   {1560.0:>6.0f} ms                      |")
        print(f"  |  SemLiFi CGFP Latency:        {avg_latency:>6.1f} ms                      |")
        print(f"  |  Latency Savings:             {latency_savings:>5.1f}%                         |")
        print(f"  +--------------------------------------------------------------------+")
        print("=" * 72)

        # Save results to JSON
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "mode": "simulation",
            "threshold": self.threshold,
            "total_frames": total,
            "clean_frames": s["clean_frames"],
            "burst_frames": burst,
            "patched_frames": patched,
            "retransmit_frames": retransmit,
            "exact_patches": exact,
            "incorrect_patches": incorrect,
            "retransmission_reduction_pct": round(retrans_reduction, 2),
            "undetected_error_rate_pct": round(ufer, 2),
            "mean_latency_ms": round(avg_latency, 1),
            "mean_inference_ms": round(avg_inference, 2),
            "state_transition_accuracy_pct": round(st_acc, 1),
            "pure_arq_latency_ms": 1560.0,
            "latency_savings_pct": round(latency_savings, 1),
            "backchannel_stats": self.backchannel.get_stats(),
        }
        report_path = os.path.join(DATA_DIR, "phase5_pipeline_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n[OK] Full pipeline report saved to: {report_path}")

        return report


def main():
    parser = argparse.ArgumentParser(description="SemLiFi Phase 5 — Full System Integration Pipeline")
    parser.add_argument("--mode", choices=["simulate", "live"], default="simulate",
                        help="Pipeline mode: 'simulate' for synthetic bursts, 'live' for real hardware")
    parser.add_argument("--frames", type=int, default=50,
                        help="Number of telemetry frames to process (default: 50)")
    parser.add_argument("--threshold", type=float, default=0.80,
                        help="CGFP confidence threshold τ* (default: 0.80)")
    parser.add_argument("--burst-prob", type=float, default=0.40,
                        help="Burst probability per frame in simulation mode (default: 0.40)")
    parser.add_argument("--sender", type=str, default="COM12",
                        help="Sender COM port for live mode (default: COM12)")
    parser.add_argument("--receiver", type=str, default="COM11",
                        help="Receiver COM port for live mode (default: COM11)")
    parser.add_argument("--device", type=str, default="cpu",
                        help="Compute device (default: cpu)")

    args = parser.parse_args()

    pipeline = SemLiFiPipeline(threshold=args.threshold, device=args.device)

    if args.mode == "simulate":
        pipeline.run_simulation(num_frames=args.frames, burst_probability=args.burst_prob)
    else:
        pipeline.run_live(sender_port=args.sender, receiver_port=args.receiver, num_frames=args.frames)


if __name__ == "__main__":
    main()

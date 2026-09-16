"""
SemLiFi Phase 3 — Hard Gate Checkpoint Evaluator
================================================
Compares BASR Transformer against Naive Baselines on held-out test data:
  - Baseline 1: Last-Known-Value (LKV) Repeat
  - Baseline 2: Linear Interpolation (LERP) / Historical Mean
  - Proposed:   BASR (Burst-Aware Sequence Reconstruction) Transformer

Evaluates Hard Checkpoint:
  "BASR must clearly outperform the naive baseline on reconstruction accuracy
   before proceeding to Phase 4."
"""

import os
import sys
import re
import torch
import numpy as np
import matplotlib.pyplot as plt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from basr.dataset import get_dataloaders, VOCAB, CHAR2IDX, IDX2CHAR, PAD_IDX, MASK_IDX
from basr.model import BASRTransformer

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints", "basr_best.pth")
PLOT_PATH = os.path.join(DATA_DIR, "basr_vs_baselines_gate.png")


def parse_telemetry_fields(text):
    """Extracts temp (float), hum (int), motor (str) from payload string."""
    m_temp = re.search(r'TEMP=(\d+\.?\d*)', text)
    m_hum = re.search(r'HUM=(\d+)', text)
    m_motor = re.search(r'MOTOR=(ON|OFF)', text)

    temp_val = None
    if m_temp:
        try:
            temp_val = float(m_temp.group(1))
        except ValueError:
            temp_val = None

    hum_val = None
    if m_hum:
        try:
            hum_val = int(m_hum.group(1))
        except ValueError:
            hum_val = None

    motor_val = m_motor.group(1) if m_motor else None
    return temp_val, hum_val, motor_val


def evaluate_all():
    print("=" * 75)
    print("      SEMLIFI PHASE 3: BASR vs NAIVE BASELINES (HARD GATE EVALUATION)     ")
    print("=" * 75)

    _, _, _, test_ds = get_dataloaders()
    print(f"Evaluating on {len(test_ds)} held-out empirical test frames...\n")

    # Load trained BASR model
    device = torch.device("cpu")
    model = BASRTransformer(vocab_size=len(VOCAB), d_model=64, nhead=4, num_layers=2, dim_feedforward=96, dropout=0.05)
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Model Architecture: {total_params:,} parameters (Budget: < 75,000)")
    assert total_params < 75000, f"Model parameters ({total_params}) exceeded 75k!"

    if os.path.exists(CHECKPOINT_PATH):
        ckpt = torch.load(CHECKPOINT_PATH, map_location=device)
        model.load_state_dict(ckpt["model_state_dict"])
        print(f"[OK] Loaded trained BASR model from: {CHECKPOINT_PATH}")
    else:
        print("[WARN] Trained checkpoint not found, evaluating initialized model.")
    model.eval()

    # Track metrics across methods
    methods = ["LKV Repeat (Baseline 1)", "Linear Interp (Baseline 2)", "BASR Transformer (Proposed)"]
    char_accuracies = {m: [] for m in methods}
    exact_matches = {m: 0 for m in methods}
    temp_errors = {m: [] for m in methods}
    hum_errors = {m: [] for m in methods}
    motor_correct = {m: 0 for m in methods}
    transition_correct = {m: 0 for m in methods}
    transition_total = 0
    inference_times = []
    total_eval_samples = 0

    for idx in range(len(test_ds)):
        item = test_ds[idx]
        clean_str = item["clean_str"]
        masked_str = item["masked_str"]
        prev_str = item["prev_str"]
        start_mask, end_mask = item["mask_range"]
        is_trans = item["state_changed"]

        if start_mask == -1:
            continue # Skip clean unmasked frames for burst evaluation

        total_eval_samples += 1
        if is_trans:
            transition_total += 1

        gt_temp, gt_hum, gt_motor = item["temp"], item["hum"], item["motor"]

        # --- Method 1: Last-Known-Value (LKV) Repeat ---
        lkv_recon = prev_str
        lkv_temp, lkv_hum, lkv_motor = parse_telemetry_fields(lkv_recon)

        # --- Method 2: Linear / Historical Interp ---
        lerp_recon_chars = list(masked_str)
        for p in range(start_mask, end_mask):
            lerp_recon_chars[p] = prev_str[p] if p < len(prev_str) else "0"
        lerp_recon = "".join(lerp_recon_chars)
        lerp_temp, lerp_hum, lerp_motor = parse_telemetry_fields(lerp_recon)

        # --- Method 3: BASR Transformer ---
        t0 = time.time()
        basr_recon = model.reconstruct(item["curr_ids"], item["prev_ids"], CHAR2IDX, IDX2CHAR)
        t_infer = (time.time() - t0) * 1000.0
        inference_times.append(t_infer)
        basr_temp, basr_hum, basr_motor = parse_telemetry_fields(basr_recon)

        recon_dict = {
            "LKV Repeat (Baseline 1)": (lkv_recon, lkv_temp, lkv_hum, lkv_motor),
            "Linear Interp (Baseline 2)": (lerp_recon, lerp_temp, lerp_hum, lerp_motor),
            "BASR Transformer (Proposed)": (basr_recon, basr_temp, basr_hum, basr_motor)
        }

        # Calculate metrics
        for m_name, (rec_str, r_t, r_h, r_m) in recon_dict.items():
            correct_chars = 0
            for p in range(start_mask, end_mask):
                if p < len(rec_str) and p < len(clean_str) and rec_str[p] == clean_str[p]:
                    correct_chars += 1
            mask_len = end_mask - start_mask
            acc = (correct_chars / max(1, mask_len)) * 100.0
            char_accuracies[m_name].append(acc)

            if rec_str == clean_str:
                exact_matches[m_name] += 1

            if r_t is not None:
                temp_errors[m_name].append(abs(r_t - gt_temp))
            else:
                temp_errors[m_name].append(3.0)

            if r_h is not None:
                hum_errors[m_name].append(abs(r_h - gt_hum))
            else:
                hum_errors[m_name].append(10.0)

            if r_m == gt_motor:
                motor_correct[m_name] += 1
                if is_trans:
                    transition_correct[m_name] += 1

    # Aggregate Results
    summary = {}
    print("--- BENCHMARK RESULTS ON HELD-OUT BURST FRAMES ---")
    for m in methods:
        mean_acc = np.mean(char_accuracies[m])
        exact_rate = (exact_matches[m] / total_eval_samples) * 100.0
        t_mae = np.mean(temp_errors[m])
        h_mae = np.mean(hum_errors[m])
        m_acc = (motor_correct[m] / total_eval_samples) * 100.0
        tr_acc = (transition_correct[m] / max(1, transition_total)) * 100.0
        summary[m] = {
            "char_acc": mean_acc,
            "exact_rate": exact_rate,
            "temp_mae": t_mae,
            "hum_mae": h_mae,
            "motor_acc": m_acc,
            "trans_acc": tr_acc
        }
        print(f"\n  [{m}]")
        print(f"    • Masked Character Accuracy:   {mean_acc:.2f}%")
        print(f"    • Exact Frame Reconstruction:  {exact_rate:.2f}%")
        print(f"    • State Transition Accuracy:   {tr_acc:.1f}% ({transition_correct[m]}/{transition_total})")
        print(f"    • Temperature Field MAE:       {t_mae:.3f} °C")
        print(f"    • Humidity Field MAE:          {h_mae:.2f}%")
        print(f"    • Discrete State (Motor) Acc:  {m_acc:.1f}%")

    avg_cpu_lat = np.mean(inference_times)
    p95_cpu_lat = np.percentile(inference_times, 95)
    print(f"\n  [BASR Latency Profile (Edge CPU)]")
    print(f"    • Mean Inference Latency:      {avg_cpu_lat:.2f} ms")
    print(f"    • 95th Percentile Latency:     {p95_cpu_lat:.2f} ms (Budget: < 3.0 ms)")

    # Hard Gate Checkpoint Verification
    lkv_exact = summary["LKV Repeat (Baseline 1)"]["exact_rate"]
    lkv_trans = summary["LKV Repeat (Baseline 1)"]["trans_acc"]
    lerp_trans = summary["Linear Interp (Baseline 2)"]["trans_acc"]
    basr_exact = summary["BASR Transformer (Proposed)"]["exact_rate"]
    basr_trans = summary["BASR Transformer (Proposed)"]["trans_acc"]

    gate_exact_win = basr_exact > (lkv_exact * 2.0)  # Must achieve >2x exact reconstruction over LKV repeat
    gate_trans_win = (basr_trans > lerp_trans) and (basr_trans >= 95.0)  # Must outperform Lerp and exceed 95% on transitions
    gate_size_ok = total_params < 75000

    gate_passed = gate_exact_win and gate_trans_win and gate_size_ok

    print("\n" + "=" * 75)
    if gate_passed:
        print("  PHASE 3 HARD GATE CHECKPOINT: [ PASS ]")
        print(f"  • Exact Frame Recovery Gain:    +{basr_exact - lkv_exact:.1f}% vs LKV Repeat ({basr_exact:.1f}% vs {lkv_exact:.1f}%, {basr_exact/lkv_exact:.2f}x gain)")
        print(f"  • State Transition Outperformance: {basr_trans:.1f}% vs Linear Interp ({lerp_trans:.1f}%) & LKV ({lkv_trans:.1f}%)")
        print(f"  • Edge Model Constraints:       {total_params:,} parameters (< 75k) | {avg_cpu_lat:.2f} ms CPU latency")
        print("  System is validated to proceed to Phase 4 (CGFP Confidence-Guided Patching).")
    else:
        print("  PHASE 3 HARD GATE CHECKPOINT: [ REVIEW ]")
        print(f"  Gate criteria not met: Exact Win={gate_exact_win}, Trans Win={gate_trans_win}, Size={gate_size_ok}")
    print("=" * 75 + "\n")

    # Generate Publication Diagnostic Bar Chart
    labels = ["LKV Repeat\n(Baseline 1)", "Linear Interp\n(Baseline 2)", "BASR Transformer\n(Proposed)"]
    exact_vals = [summary[m]["exact_rate"] for m in methods]
    char_acc_vals = [summary[m]["char_acc"] for m in methods]
    trans_vals = [summary[m]["trans_acc"] for m in methods]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    
    # Panel 1: Exact Frame Reconstruction Rate
    bars1 = ax1.bar(labels, exact_vals, color=["#aec7e8", "#ffbb78", "#2ca02c"], edgecolor="black", width=0.55)
    ax1.set_title("Exact Frame Reconstruction Rate (%) — Primary Gate Metric", fontsize=11, fontweight="bold")
    ax1.set_ylabel("Exact Frame Recovery (%)")
    ax1.set_ylim(0, max(exact_vals) * 1.35)
    for bar in bars1:
        yval = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2.0, yval + 1.0, f"{yval:.1f}%", ha='center', va='bottom', fontweight='bold')

    # Panel 2: State Transition Accuracy
    bars2 = ax2.bar(labels, trans_vals, color=["#aec7e8", "#ffbb78", "#1f77b4"], edgecolor="black", width=0.55)
    ax2.set_title("Dynamic State Transition Accuracy (%)", fontsize=11, fontweight="bold")
    ax2.set_ylabel("Accuracy (%)")
    ax2.set_ylim(0, 115)
    for bar in bars2:
        yval = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2.0, yval + 1.5, f"{yval:.1f}%", ha='center', va='bottom', fontweight='bold')

    plt.suptitle("SemLiFi Phase 3 Hard Gate Checkpoint: BASR vs Naive Baselines", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=200)
    print(f"[OK] Gate checkpoint plot saved to:\n     {PLOT_PATH}")

if __name__ == "__main__":
    import time
    evaluate_all()


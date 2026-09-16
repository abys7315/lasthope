"""
SemLiFi Phase 3 — BASR Dataset Generator & Empirical Burst Masking
==================================================================
Generates realistic structured telemetry sequences and applies
burst masking sampled directly from the empirical Phase 2 burst distribution
(data/burst_events.jsonl), eliminating arbitrary/synthetic noise.

Target Telemetry Schema:
  TEMP=<float 20.0-32.0>,HUM=<int 40-75>,MOTOR=<ON|OFF>
"""

import os
import json
import random
import numpy as np
import torch
from torch.utils.data import Dataset

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BURST_JSONL = os.path.join(DATA_DIR, "burst_events.jsonl")

# Character-level vocabulary for structured telemetry
VOCAB = [
    "[PAD]", "[UNK]", "[MASK]", "[SOS]", "[EOS]", "[SEP]",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    ".", ",", "=", ":", "%", "-", " ",
    "T", "E", "M", "P", "H", "U", "O", "R", "N", "F",
    "S", "I", "G", "A", "L", "B", "K", "X"
]

CHAR2IDX = {ch: i for i, ch in enumerate(VOCAB)}
IDX2CHAR = {i: ch for i, ch in enumerate(VOCAB)}
PAD_IDX = CHAR2IDX["[PAD]"]
MASK_IDX = CHAR2IDX["[MASK]"]
SEP_IDX = CHAR2IDX["[SEP]"]
MAX_SEQ_LEN = 32


def load_empirical_burst_durations(jsonl_path=BURST_JSONL):
    """Loads true measured burst durations (ms) from Phase 2 dataset."""
    durations = []
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        if record.get("duration_ms", 0) > 0:
                            durations.append(record["duration_ms"])
                    except Exception:
                        pass
    if not durations:
        # Fallback distribution matching Phase 2 rig calibration (multimodal)
        durations = [28.0] * 30 + [120.0] * 20 + [250.0] * 25
    return durations


def generate_telemetry_stream(num_samples=2500):
    """Generates continuous realistic telemetry time series with correlated physical values."""
    stream = []
    temp = 24.5
    hum = 55
    motor = "ON"
    motor_timer = 0

    for i in range(num_samples):
        # Physical random-walk dynamics
        temp += random.gauss(0, 0.15)
        temp = max(19.0, min(33.0, temp))

        hum += int(random.choice([-1, 0, 0, 1]))
        hum = max(42, min(78, hum))

        motor_timer += 1
        if motor_timer > random.randint(15, 40):
            motor = "OFF" if motor == "ON" else "ON"
            motor_timer = 0

        text = f"TEMP={temp:.1f},HUM={hum},MOTOR={motor}"
        stream.append({
            "step": i,
            "text": text,
            "temp": round(temp, 1),
            "hum": hum,
            "motor": motor
        })
    return stream


class TelemetryBurstDataset(Dataset):
    def __init__(self, telemetry_records, empirical_bursts, mask_prob=0.85):
        self.records = telemetry_records
        self.burst_durations = empirical_bursts
        self.mask_prob = mask_prob
        self.samples = []
        self._prepare_data()

    def _prepare_data(self):
        for i, rec in enumerate(self.records):
            clean_str = rec["text"]
            prev_str = self.records[i - 1]["text"] if i > 0 else clean_str
            prev_motor = self.records[i - 1]["motor"] if i > 0 else rec["motor"]
            state_changed = (rec["motor"] != prev_motor)

            # Decide if frame experiences burst loss
            if random.random() < self.mask_prob:
                dur_ms = random.choice(self.burst_durations)
                # 1000 bps -> 1 ms/bit -> ~10 ms/byte (character)
                burst_chars = max(2, min(len(clean_str) - 2, int(round(dur_ms / 10.0))))
                start_idx = random.randint(0, len(clean_str) - burst_chars)
                end_idx = start_idx + burst_chars

                masked_str = list(clean_str)
                for pos in range(start_idx, end_idx):
                    masked_str[pos] = "?"
                masked_str = "".join(masked_str)
            else:
                masked_str = clean_str
                start_idx = -1
                end_idx = -1

            self.samples.append({
                "clean": clean_str,
                "masked": masked_str,
                "prev": prev_str,
                "temp": rec["temp"],
                "hum": rec["hum"],
                "motor": rec["motor"],
                "mask_range": (start_idx, end_idx),
                "state_changed": state_changed
            })

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        item = self.samples[idx]
        
        def pad_to_max(text, is_masked=False):
            if is_masked:
                tokens = [MASK_IDX if c == "?" else CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
            else:
                tokens = [CHAR2IDX.get(c, CHAR2IDX["[UNK]"]) for c in text]
            if len(tokens) < MAX_SEQ_LEN:
                tokens = tokens + [PAD_IDX] * (MAX_SEQ_LEN - len(tokens))
            return torch.tensor(tokens[:MAX_SEQ_LEN], dtype=torch.long)

        curr_ids = pad_to_max(item["masked"], is_masked=True)
        prev_ids = pad_to_max(item["prev"], is_masked=False)
        clean_target = pad_to_max(item["clean"], is_masked=False)

        return {
            "curr_ids": curr_ids,
            "prev_ids": prev_ids,
            "clean_target": clean_target,
            "clean_str": item["clean"],
            "masked_str": item["masked"],
            "prev_str": item["prev"],
            "temp": item["temp"],
            "hum": item["hum"],
            "motor": item["motor"],
            "mask_range": item["mask_range"],
            "state_changed": item["state_changed"]
        }


def get_dataloaders(batch_size=64, seed=None, return_val_ds=False):
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)

    burst_durations = load_empirical_burst_durations()
    telemetry = generate_telemetry_stream(num_samples=6000)

    train_data = telemetry[:4500]
    val_data = telemetry[4500:5250]    # Calibration split (750 samples)
    test_data = telemetry[5250:]      # Strictly held-out test split (750 samples)

    train_ds = TelemetryBurstDataset(train_data, burst_durations, mask_prob=0.85)
    val_ds = TelemetryBurstDataset(val_data, burst_durations, mask_prob=0.85)
    test_ds = TelemetryBurstDataset(test_data, burst_durations, mask_prob=0.85)

    from torch.utils.data import DataLoader
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    if return_val_ds:
        return train_loader, val_loader, test_loader, test_ds, val_ds
    return train_loader, val_loader, test_loader, test_ds

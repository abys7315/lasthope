"""
SemLiFi Phase 3 — BASR Model Training Engine
============================================
Trains the lightweight Transformer on empirical burst-masked telemetry sequences.
Saves the best model checkpoint for evaluation against naive baselines.
"""

import os
import sys
import time
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from basr.dataset import get_dataloaders, VOCAB, PAD_IDX, MASK_IDX
from basr.model import BASRTransformer

CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")
BEST_MODEL_PATH = os.path.join(CHECKPOINT_DIR, "basr_best.pth")

def train_basr(epochs=12, lr=0.003, batch_size=64):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("=" * 75)
    print("           SEMLIFI PHASE 3: BASR MODEL TRAINING (TRANSFORMER)             ")
    print("=" * 75)
    print(f"Device: {device} | Epochs: {epochs} | Batch Size: {batch_size} | Vocab Size: {len(VOCAB)}")

    train_loader, val_loader, test_loader, _ = get_dataloaders(batch_size=batch_size)

    model = BASRTransformer(vocab_size=len(VOCAB), d_model=64, nhead=4, num_layers=2, dim_feedforward=96, dropout=0.05)
    model.to(device)

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total Trainable Parameters: {total_params:,} (Ultra-lightweight edge model < 75k)\n")

    criterion = nn.CrossEntropyLoss(ignore_index=PAD_IDX)
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_acc = 0.0
    start_time = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        train_tokens = 0

        for batch in train_loader:
            curr = batch["curr_ids"].to(device)
            prev = batch["prev_ids"].to(device)
            target = batch["clean_target"].to(device)

            optimizer.zero_grad()
            logits = model(curr, prev)
            loss = criterion(logits.view(-1, len(VOCAB)), target.view(-1))
            loss.backward()
            optimizer.step()

            train_loss += loss.item() * curr.size(0)
            train_tokens += curr.size(0)

        scheduler.step()
        avg_train_loss = train_loss / train_tokens

        # Validation
        model.eval()
        val_loss = 0.0
        val_tokens = 0
        correct_chars = 0
        total_chars = 0

        with torch.no_grad():
            for batch in val_loader:
                curr = batch["curr_ids"].to(device)
                prev = batch["prev_ids"].to(device)
                target = batch["clean_target"].to(device)

                logits = model(curr, prev)
                mask_positions = (curr == MASK_IDX)

                if mask_positions.sum() > 0:
                    v_loss = criterion(logits[mask_positions], target[mask_positions])
                    val_loss += v_loss.item() * curr.size(0)
                    val_tokens += curr.size(0)

                    preds = torch.argmax(logits[mask_positions], dim=-1)
                    correct_chars += (preds == target[mask_positions]).sum().item()
                    total_chars += mask_positions.sum().item()

        avg_val_loss = val_loss / max(1, val_tokens)
        val_acc = (correct_chars / max(1, total_chars)) * 100.0

        print(f"  Epoch {epoch:02d}/{epochs:02d} | Train Loss: {avg_train_loss:.4f} | "
              f"Val Loss: {avg_val_loss:.4f} | Masked Char Acc: {val_acc:.1f}%")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save({
                "model_state_dict": model.state_dict(),
                "vocab": VOCAB,
                "val_acc": val_acc,
                "epoch": epoch
            }, BEST_MODEL_PATH)

    elapsed = time.time() - start_time
    print("-" * 75)
    print(f"Training Complete in {elapsed:.1f}s | Best Val Acc: {best_val_acc:.2f}% | Best Checkpoint: {BEST_MODEL_PATH}")
    print("=" * 75 + "\n")
    return model

if __name__ == "__main__":
    train_basr(epochs=12, lr=0.003, batch_size=64)


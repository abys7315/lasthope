"""
SemLiFi Phase 4 — CGFP (Confidence-Guided Frame Patching) Confidence Engine
==========================================================================
Computes calibrated multi-factor confidence scores for BASR-reconstructed frames.
Combines:
  1. Token-level posterior probabilities from BASR softmax outputs.
  2. Non-linear burst duration penalty (longer occlusions carry higher risk).
  3. Strict industrial telemetry syntax validation (schema conformity).
  4. Physical telemetry boundary verification.
"""

import math
import re
import torch

TELEMETRY_REGEX = re.compile(r"^TEMP=(\d+\.\d+),HUM=(\d+),MOTOR=(ON|OFF)$")

def validate_telemetry_syntax(text):
    """
    Validates both schema syntax and physical boundary plausibility.
    Returns (is_valid, parsed_dict)
    """
    m = TELEMETRY_REGEX.match(text)
    if not m:
        return False, None
    
    try:
        temp = float(m.group(1))
        hum = int(m.group(2))
        motor = m.group(3)
    except ValueError:
        return False, None
    
    # Industrial physical plausibility bounds
    if not (10.0 <= temp <= 45.0):
        return False, None
    if not (20 <= hum <= 95):
        return False, None
    if motor not in ("ON", "OFF"):
        return False, None
        
    return True, {"temp": temp, "hum": hum, "motor": motor}


def compute_frame_confidence(logits, curr_ids, prev_ids, reconstructed_str, mask_idx=2, pad_idx=0):
    """
    Calculates composite frame patching confidence C_frame in [0.0, 1.0].
    
    Args:
        logits: Tensor of shape (1, seq_len, vocab_size) or (seq_len, vocab_size)
        curr_ids: Tensor of shape (seq_len,) with [MASK] at corrupted positions
        prev_ids: Tensor of shape (seq_len,)
        reconstructed_str: Decoded string produced by BASR
        mask_idx: Vocabulary index of [MASK] token
        pad_idx: Vocabulary index of [PAD] token
        
    Returns:
        dict containing:
            - frame_confidence: float in [0.0, 1.0]
            - token_confidence: geometric mean of masked token probabilities
            - burst_penalty: penalty factor based on occlusion span
            - syntax_valid: bool indicating schema conformity
            - token_probs: list of (position, predicted_token, probability)
            - parsed_telemetry: dict of validated fields or None
    """
    if logits.dim() == 3:
        logits = logits[0]
    if curr_ids.dim() == 2:
        curr_ids = curr_ids[0]
        
    probs = torch.softmax(logits, dim=-1)
    preds = torch.argmax(logits, dim=-1)
    
    # 1. Identify masked positions and extract token-level posterior probabilities
    masked_indices = (curr_ids == mask_idx).nonzero(as_tuple=True)[0].tolist()
    token_probs = []
    
    if not masked_indices:
        # No corrupted positions: frame is pristine
        return {
            "frame_confidence": 1.0,
            "token_confidence": 1.0,
            "burst_penalty": 1.0,
            "syntax_valid": True,
            "token_probs": [],
            "burst_len": 0,
            "parsed_telemetry": validate_telemetry_syntax(reconstructed_str)[1]
        }
        
    log_sum = 0.0
    for idx in masked_indices:
        pred_token = preds[idx].item()
        p = probs[idx, pred_token].item()
        p_clamped = max(1e-6, min(1.0, p))
        log_sum += math.log(p_clamped)
        token_probs.append({
            "position": idx,
            "token_id": pred_token,
            "probability": p
        })
        
    # Geometric mean of token probabilities across the masked span
    token_conf = math.exp(log_sum / len(masked_indices))
    
    # 2. Burst length risk penalty
    # Nominal payload length is 25 bytes.
    # Corruptions > 15 bytes carry exponentially higher semantic risk.
    burst_len = len(masked_indices)
    burst_ratio = burst_len / 25.0
    # Penalty decreases from 1.0 down to ~0.35 as burst ratio approaches 1.0
    burst_penalty = max(0.20, 1.0 - 0.50 * math.pow(burst_ratio, 1.15))
    
    # 3. Schema and syntax validation
    is_valid_syntax, parsed_fields = validate_telemetry_syntax(reconstructed_str)
    syntax_factor = 1.0 if is_valid_syntax else 0.0
    
    # 4. Composite Confidence Score
    frame_confidence = token_conf * burst_penalty * syntax_factor
    
    return {
        "frame_confidence": round(frame_confidence, 4),
        "token_confidence": round(token_conf, 4),
        "burst_penalty": round(burst_penalty, 4),
        "syntax_valid": is_valid_syntax,
        "token_probs": token_probs,
        "burst_len": burst_len,
        "parsed_telemetry": parsed_fields
    }

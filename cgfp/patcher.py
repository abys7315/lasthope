"""
SemLiFi Phase 4 — CGFP Frame Patching Engine
============================================
The operational runtime engine that decides whether to patch burst-corrupted
optical frames on-device or trigger a back-channel retransmission request.
"""

import os
import sys
import time
import torch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from basr.model import BASRTransformer, MAX_SEQ_LEN
from basr.dataset import VOCAB, CHAR2IDX, IDX2CHAR, PAD_IDX, MASK_IDX
from cgfp.confidence import compute_frame_confidence, validate_telemetry_syntax

DEFAULT_CHECKPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "basr", "checkpoints", "basr_best.pth")

class CGFPPatcher:
    def __init__(self, checkpoint_path=DEFAULT_CHECKPOINT, default_threshold=0.75, device="cpu"):
        self.device = torch.device(device)
        self.threshold = default_threshold
        self.char2idx = CHAR2IDX
        self.idx2char = IDX2CHAR
        
        # Initialize BASR model (< 75k parameter edge budget)
        self.model = BASRTransformer(
            vocab_size=len(VOCAB),
            d_model=64,
            nhead=4,
            num_layers=2,
            dim_feedforward=96,
            dropout=0.05,
            max_len=MAX_SEQ_LEN
        )
        self.model.to(self.device)
        
        if os.path.exists(checkpoint_path):
            ckpt = torch.load(checkpoint_path, map_location=self.device)
            self.model.load_state_dict(ckpt["model_state_dict"])
            self.checkpoint_loaded = True
        else:
            self.checkpoint_loaded = False
            print(f"[WARN] Checkpoint not found at: {checkpoint_path}")
            
        self.model.eval()

    def _tokenize(self, text, is_masked=False):
        if is_masked:
            tokens = [MASK_IDX if c == "?" else self.char2idx.get(c, self.char2idx["[UNK]"]) for c in text]
        else:
            tokens = [self.char2idx.get(c, self.char2idx["[UNK]"]) for c in text]
        if len(tokens) < MAX_SEQ_LEN:
            tokens = tokens + [PAD_IDX] * (MAX_SEQ_LEN - len(tokens))
        return torch.tensor(tokens[:MAX_SEQ_LEN], dtype=torch.long, device=self.device)

    def patch_frame(self, curr_input, prev_input, threshold=None):
        """
        Executes confidence-guided patching on an incoming optical frame.
        
        Args:
            curr_input: Corrupted string with '?' at burst bytes, or tensor of token IDs
            prev_input: Last valid clean frame string, or tensor of token IDs
            threshold: Optional override for decision boundary tau in [0.0, 1.0]
            
        Returns:
            dict containing:
                - action: "PATCH" (accepted on-device) or "RETRANSMIT" (back-channel NACK)
                - frame_confidence: composite confidence C_frame
                - patched_payload: reconstructed string
                - raw_received: original corrupted string
                - latency_ms: CPU execution time
                - confidence_details: detailed sub-metrics
        """
        if threshold is None:
            threshold = self.threshold
            
        t_start = time.perf_counter()
        
        # Prepare tensors
        if isinstance(curr_input, str):
            raw_curr_str = curr_input
            curr_ids = self._tokenize(curr_input, is_masked=True)
        else:
            curr_ids = curr_input.to(self.device)
            raw_curr_str = "".join([self.idx2char.get(i.item(), "?") for i in curr_ids if i.item() != PAD_IDX])
            
        if isinstance(prev_input, str):
            raw_prev_str = prev_input
            prev_ids = self._tokenize(prev_input, is_masked=False)
        else:
            prev_ids = prev_input.to(self.device)
            raw_prev_str = "".join([self.idx2char.get(i.item(), "") for i in prev_ids if i.item() != PAD_IDX])
            
        # 1. Run BASR model inference
        with torch.no_grad():
            if curr_ids.dim() == 1:
                c_in = curr_ids.unsqueeze(0)
                p_in = prev_ids.unsqueeze(0)
            else:
                c_in = curr_ids
                p_in = prev_ids
                
            logits = self.model(c_in, p_in)
            preds = torch.argmax(logits, dim=-1)[0].tolist()
            
            # Reconstruct string
            raw_curr = curr_ids if curr_ids.dim() == 1 else curr_ids[0]
            raw_ids = raw_curr.tolist()
            out_chars = []
            for c_id, p_id in zip(raw_ids, preds):
                if c_id == PAD_IDX:
                    break
                if c_id == MASK_IDX:
                    out_chars.append(self.idx2char.get(p_id, "?"))
                else:
                    out_chars.append(self.idx2char.get(c_id, "?"))
            reconstructed_str = "".join(out_chars)
            
        # 2. Compute CGFP confidence metric
        conf_metrics = compute_frame_confidence(
            logits=logits,
            curr_ids=curr_ids,
            prev_ids=prev_ids,
            reconstructed_str=reconstructed_str,
            mask_idx=MASK_IDX,
            pad_idx=PAD_IDX
        )
        
        c_frame = conf_metrics["frame_confidence"]
        
        # 3. Decision Boundary: Patch on-device vs Request Retransmission
        if c_frame >= threshold and conf_metrics["syntax_valid"]:
            action = "PATCH"
        else:
            action = "RETRANSMIT"
            
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        
        return {
            "action": action,
            "frame_confidence": c_frame,
            "threshold": threshold,
            "patched_payload": reconstructed_str,
            "raw_received": raw_curr_str,
            "prev_reference": raw_prev_str,
            "latency_ms": round(elapsed_ms, 3),
            "confidence_details": conf_metrics
        }

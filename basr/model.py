"""
SemLiFi Phase 3 — BASR (Burst-Aware Sequence Reconstruction) Model
==================================================================
A lightweight, distilled Transformer sequence-to-sequence model
designed explicitly for edge CPU inference (< 3ms per frame).
Reconstructs contiguous masked tokens caused by optical burst occlusions
using intra-frame positional alignment and temporal history conditioning.
"""

import torch
import torch.nn as nn

MAX_SEQ_LEN = 32

class BASRTransformer(nn.Module):
    def __init__(self, vocab_size=41, d_model=64, nhead=4, num_layers=2, dim_feedforward=96, dropout=0.05, max_len=MAX_SEQ_LEN):
        super().__init__()
        self.vocab_size = vocab_size
        self.d_model = d_model
        self.max_len = max_len
        
        self.tok_emb = nn.Embedding(vocab_size, d_model, padding_idx=0)
        self.pos_emb = nn.Embedding(max_len, d_model)
        self.fuse_proj = nn.Linear(d_model * 2, d_model)
        
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
            activation="gelu"
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc_out = nn.Linear(d_model, vocab_size)

    def forward(self, curr_ids, prev_ids, pad_mask=None):
        """
        curr_ids: (batch_size, seq_len) with [MASK] at burst positions
        prev_ids: (batch_size, seq_len) previous successfully received frame
        """
        b = curr_ids.size(0)
        pos = torch.arange(self.max_len, device=curr_ids.device).unsqueeze(0).expand(b, -1)
        
        c_emb = self.tok_emb(curr_ids) + self.pos_emb(pos)
        p_emb = self.tok_emb(prev_ids) + self.pos_emb(pos)
        
        fused = self.fuse_proj(torch.cat([c_emb, p_emb], dim=-1))
        if pad_mask is None:
            pad_mask = (curr_ids == 0) & (prev_ids == 0)
        encoded = self.transformer(fused, src_key_padding_mask=pad_mask)
        logits = self.fc_out(encoded)
        return logits

    def reconstruct(self, curr_ids, prev_ids, char2idx, idx2char):
        """Inference function: reconstructs masked tokens using highest probability predictions."""
        self.eval()
        with torch.no_grad():
            if curr_ids.dim() == 1:
                curr_ids = curr_ids.unsqueeze(0)
                prev_ids = prev_ids.unsqueeze(0)
            logits = self.forward(curr_ids, prev_ids)
            preds = torch.argmax(logits, dim=-1)[0].tolist()
            raw_curr = curr_ids[0].tolist()
            
            mask_idx = char2idx.get("[MASK]", 2)
            pad_idx = char2idx.get("[PAD]", 0)
            
            reconstructed_chars = []
            for c_id, p_id in zip(raw_curr, preds):
                if c_id == pad_idx:
                    break
                if c_id == mask_idx:
                    reconstructed_chars.append(idx2char.get(p_id, "?"))
                else:
                    reconstructed_chars.append(idx2char.get(c_id, "?"))
            return "".join(reconstructed_chars)


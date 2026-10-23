"""bge-m3 稠密向量：transformers 直接加载，取 CLS 向量并 L2 归一化（与官方 dense 用法一致）。"""

from __future__ import annotations

import numpy as np

MODEL = "BAAI/bge-m3"


def pick_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class BgeM3Encoder:
    def __init__(self, device: str | None = None, batch_size: int = 16, max_length: int = 512):
        from transformers import AutoModel, AutoTokenizer

        self.device = device or pick_device()
        self.batch_size, self.max_length = batch_size, max_length
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModel.from_pretrained(MODEL).to(self.device).eval()

    def encode(self, texts: list[str]) -> np.ndarray:
        import torch

        out = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(
                    texts[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                ).to(self.device)
                cls = self.model(**batch).last_hidden_state[:, 0]
                out.append(torch.nn.functional.normalize(cls, dim=-1).float().cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 1024), dtype=np.float32)

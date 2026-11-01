"""bge-m3 稠密向量：transformers 直接加载，取 CLS 向量并 L2 归一化（与官方 dense 用法一致）。"""

from __future__ import annotations

import numpy as np

MODEL = "BAAI/bge-m3"


def length_sorted_batches(texts: list[str], batch_size: int):
    """按长度从长到短分批，减少补齐的浪费；返回每批的原始下标，调用方据此还原顺序。"""
    order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
    for i in range(0, len(order), batch_size):
        yield order[i : i + batch_size]


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
        if self.device == "mps":
            self.model = self.model.half()  # MPS 上 fp16 约快一倍，归一化后的向量误差可忽略

    def encode(self, texts: list[str]) -> np.ndarray:
        import torch

        out = np.zeros((len(texts), 1024), dtype=np.float32)
        with torch.inference_mode():
            for idx in length_sorted_batches(texts, self.batch_size):
                batch = self.tokenizer(
                    [texts[i] for i in idx],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                ).to(self.device)
                cls = self.model(**batch).last_hidden_state[:, 0].float()
                out[idx] = torch.nn.functional.normalize(cls, dim=-1).cpu().numpy()
        return out

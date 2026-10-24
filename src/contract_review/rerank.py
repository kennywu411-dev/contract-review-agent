"""bge-reranker-v2-m3 交叉编码器重排，输出 sigmoid 后的 0–1 分数（D4 用它定拒答阈值）。"""

from __future__ import annotations

from contract_review.embed import pick_device

MODEL = "BAAI/bge-reranker-v2-m3"


class BgeReranker:
    def __init__(self, device: str | None = None, batch_size: int = 16, max_length: int = 512):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.device = device or pick_device()
        self.batch_size, self.max_length = batch_size, max_length
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL)
        self.model = self.model.to(self.device).eval()
        if self.device == "mps":
            self.model = self.model.half()

    def score(self, query: str, texts: list[str]) -> list[float]:
        import torch

        out: list[float] = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                pairs = [[query, t] for t in texts[i : i + self.batch_size]]
                batch = self.tokenizer(
                    pairs,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                ).to(self.device)
                logits = self.model(**batch).logits.view(-1).float()
                out += torch.sigmoid(logits).cpu().tolist()
        return out

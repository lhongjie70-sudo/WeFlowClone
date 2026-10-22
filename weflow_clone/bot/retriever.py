"""轻量检索：从你的历史聊天里找出「类似场景下你以前怎么回」。

不做向量库、不装依赖，用 BM25 在「对方问 → 我答」的配对集合上检索。
中文按字符二元组切分（不需要分词器），英文/数字按词切。

用途：每次提问先检索 2~3 条最相似的历史对话，作为 few-shot 塞进上下文，
让模型模仿「你在这个话题上的具体答法」，而不是泛泛地"像你"。
"""

from __future__ import annotations

import csv
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import Settings

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class Example:
    question: str
    answer: str
    session: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"question": self.question, "answer": self.answer, "session": self.session}


def tokenize(text: str) -> list[str]:
    """中文二元组 + 英文单词 + 单字兜底。"""
    text = (text or "").strip().lower()
    if not text:
        return []
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text):
        if _CJK.match(chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    return tokens


def _parse_time(value: str) -> float:
    text = (value or "").strip()
    if not text:
        return 0.0
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def load_examples(settings: Settings, *, max_examples: int = 30000) -> list[Example]:
    """扫描 dataset/csv，收集「对方一句 + 我回一句（5 分钟内）」。"""
    root = settings.csv_dir
    if not root.is_dir():
        return []
    examples: list[Example] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        rows: list[tuple[float, bool, str]] = []
        for path in sorted(folder.glob("*.csv")):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        if (row.get("type_name") or "").lower() != "text":
                            continue
                        text = (row.get("msg") or "").strip()
                        if not text or len(text) > 200:
                            continue
                        rows.append(
                            (_parse_time(row.get("CreateTime") or ""), (row.get("is_sender") or "0") == "1", text)
                        )
            except (OSError, csv.Error):
                continue
        rows.sort(key=lambda item: item[0])
        pending: str | None = None
        pending_at = 0.0
        for stamp, is_me, text in rows:
            if not is_me:
                pending, pending_at = text, stamp
                continue
            if pending and (stamp - pending_at) <= 300:
                examples.append(Example(question=pending, answer=text, session=folder.name))
            pending = None
            if len(examples) >= max_examples:
                return examples
    return examples


class ExampleIndex:
    """BM25 索引；首次检索时才构建（避免启动变慢）。"""

    def __init__(self, examples: list[Example], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.examples = examples
        self.k1 = k1
        self.b = b
        self._docs: list[Counter[str]] = []
        self._lengths: list[int] = []
        self._idf: dict[str, float] = {}
        self._avg_len = 0.0
        self._built = False
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ExampleIndex":
        return cls(load_examples(settings))

    # ------------------------------------------------------------------ #
    def build(self) -> None:
        with self._lock:
            if self._built:
                return
            df: Counter[str] = Counter()
            for example in self.examples:
                tokens = tokenize(f"{example.question} {example.answer}")
                counter = Counter(tokens)
                self._docs.append(counter)
                self._lengths.append(sum(counter.values()))
                df.update(counter.keys())
            total = max(1, len(self._docs))
            self._avg_len = sum(self._lengths) / total if self._lengths else 0.0
            self._idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in df.items()}
            self._built = True

    @property
    def size(self) -> int:
        return len(self.examples)

    def search(self, query: str, top_k: int = 3) -> list[Example]:
        if top_k <= 0 or not self.examples:
            return []
        self.build()
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores: list[tuple[float, int]] = []
        avg_len = self._avg_len or 1.0
        for index, doc in enumerate(self._docs):
            score = 0.0
            length = self._lengths[index] or 1
            for token in query_tokens:
                freq = doc.get(token, 0)
                if not freq:
                    continue
                idf = self._idf.get(token, 0.0)
                denominator = freq + self.k1 * (1 - self.b + self.b * length / avg_len)
                score += idf * (freq * (self.k1 + 1)) / denominator
            if score > 0:
                scores.append((score, index))
        scores.sort(reverse=True)
        return [self.examples[index] for _score, index in scores[:top_k]]

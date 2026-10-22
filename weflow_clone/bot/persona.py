"""从聊天记录里提炼「你的说话方式」，变成一段 system prompt。

为什么不用微调也能像？
----------------------
线上模型本来就会聊天，缺的不是语言能力，而是**你的具体习惯**：
句子多长、常用哪些语气词、会不会用表情、被问到不确定的事怎么回。

所以这里只做四件事：

1. 统计（句长、疑问比例、表情比例、口头禅）；
2. 从真实对话里挑几条「对方问 → 我答」当范例；
3. 写死几条行为规则（简短、不客服腔、不编造）；
4. 拼成 system prompt，每次请求带上。

统计结果缓存在 ``<data>/bot/persona.json``，聊天记录变了就 ``wfc bot --reindex``。
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..config import Settings

_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]+"
)
_PARTICLES = ["啊", "吧", "呢", "哈", "呀", "嗯", "哦", "诶", "啦", "嘛", "呗", "咯"]
_QUESTION_TAIL = ("？", "?", "吗", "么", "呢")
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class Persona:
    message_count: int = 0
    partner_count: int = 0
    avg_length: float = 0.0
    short_ratio: float = 0.0
    question_ratio: float = 0.0
    emoji_ratio: float = 0.0
    particles: list[tuple[str, int]] = field(default_factory=list)
    phrases: list[tuple[str, int]] = field(default_factory=list)
    exemplars: list[tuple[str, str]] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    generated_at: str = ""
    prompt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Persona":
        known = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})


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


def _iter_rows(settings: Settings) -> Iterable[tuple[str, list[dict[str, str]]]]:
    root = settings.csv_dir
    if not root.is_dir():
        return
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        rows: list[dict[str, str]] = []
        for path in sorted(folder.glob("*.csv")):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        rows.append(row)
            except (OSError, csv.Error):
                continue
        if rows:
            yield folder.name, rows


def _collect_pairs(rows: list[dict[str, str]], window_seconds: float = 300.0) -> list[tuple[str, str]]:
    """把「对方说一句 + 我在 5 分钟内回一句」组成样例。"""
    timed: list[tuple[float, bool, str]] = []
    for row in rows:
        if (row.get("type_name") or "").lower() != "text":
            continue
        text = (row.get("msg") or "").strip()
        if not text:
            continue
        timed.append((_parse_time(row.get("CreateTime") or ""), (row.get("is_sender") or "0") == "1", text))
    timed.sort(key=lambda item: item[0])

    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    pending_at = 0.0
    for stamp, is_me, text in timed:
        if not is_me:
            pending, pending_at = text, stamp
            continue
        if pending and (stamp - pending_at) <= window_seconds:
            pairs.append((pending, text))
        pending = None
    return pairs


def _phrases(messages: list[str], n_min: int = 2, n_max: int = 4, top: int = 12) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for message in messages:
        cleaned = re.sub(r"\s+", " ", message)
        if len(cleaned) < n_min:
            continue
        for size in range(n_min, min(n_max, len(cleaned)) + 1):
            for start in range(0, len(cleaned) - size + 1):
                piece = cleaned[start : start + size]
                if piece.strip() and not piece.isdigit():
                    counter[piece] += 1
    # 去掉被更长短语完全覆盖的短串
    items = [(text, count) for text, count in counter.most_common(400) if count >= 3]
    result: list[tuple[str, int]] = []
    for text, count in items:
        if any(text != other and text in other and count <= other_count for other, other_count in items[:80]):
            continue
        result.append((text, count))
        if len(result) >= top:
            break
    return result


def build_persona(settings: Settings, *, max_exemplars: int = 6) -> Persona:
    mine: list[str] = []
    theirs = 0
    sessions: list[str] = []
    exemplar_pool: list[tuple[str, str]] = []
    particle_counter: Counter[str] = Counter()
    question = 0
    short = 0
    emoji = 0

    for name, rows in _iter_rows(settings):
        sessions.append(name)
        for row in rows:
            if (row.get("type_name") or "").lower() == "text" and (row.get("is_sender") or "0") != "1":
                theirs += 1
        session_mine = [
            (row.get("msg") or "").strip()
            for row in rows
            if (row.get("is_sender") or "0") == "1" and (row.get("type_name") or "").lower() == "text"
        ]
        session_mine = [text for text in session_mine if text]
        mine.extend(session_mine)
        for text in session_mine:
            if text.endswith(_QUESTION_TAIL):
                question += 1
            if len(text) <= 8:
                short += 1
            if _EMOJI.search(text):
                emoji += 1
            for particle in _PARTICLES:
                if text.endswith(particle):
                    particle_counter[particle] += 1
        pairs = _collect_pairs(rows)
        if pairs:
            step = max(1, len(pairs) // max(1, max_exemplars // 2))
            exemplar_pool.extend(pairs[::step][: max_exemplars])

    total = len(mine)
    persona = Persona(
        message_count=total,
        partner_count=theirs,
        sessions=sessions,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    if total == 0:
        persona.prompt = ""
        return persona

    persona.avg_length = round(sum(len(text) for text in mine) / total, 1)
    persona.short_ratio = round(short / total, 2)
    persona.question_ratio = round(question / total, 2)
    persona.emoji_ratio = round(emoji / total, 2)
    persona.particles = particle_counter.most_common(6)
    persona.phrases = _phrases(mine)

    # 挑示例：优先短、有来有回，且答案长度适中
    def score(pair: tuple[str, str]) -> tuple[int, int]:
        question_text, answer = pair
        good_len = 2 <= len(answer) <= 30
        return (0 if good_len else 1, abs(len(answer) - 8))

    picked = sorted(exemplar_pool, key=score)[:max_exemplars]
    persona.exemplars = picked

    persona.prompt = compose_prompt(persona)
    return persona


def compose_prompt(persona: Persona, *, bot_name: str = "", extra_system: str = "") -> str:
    name = bot_name.strip() or "我"
    lines: list[str] = [
        f"你在微信里扮演「{name}」——一个真实的人，不是 AI 助手。",
        "",
        "【你的说话习惯】",
        f"- 平均每条 {persona.avg_length:g} 个字，{int(persona.short_ratio * 100)}% 的消息不超过 8 个字，"
        "更喜欢短句、分段回，而不是一口气说完。",
    ]
    if persona.question_ratio >= 0.1:
        lines.append(f"- 大约 {int(persona.question_ratio * 100)}% 的消息是反问或确认（「吗」「？」结尾）。")
    if persona.emoji_ratio >= 0.05:
        lines.append(f"- 约 {int(persona.emoji_ratio * 100)}% 的消息会用表情符号，但不会每句都加。")
    else:
        lines.append("- 基本不用表情符号，靠语气词表达情绪。")
    if persona.particles:
        detail = "、".join(f"{word}({count})" for word, count in persona.particles)
        lines.append(f"- 常用语气词：{detail}。")
    if persona.phrases:
        detail = "、".join(f"「{text}」" for text, _count in persona.phrases[:8])
        lines.append(f"- 常挂嘴边的说法：{detail}。")

    if persona.exemplars:
        lines.extend(["", "【你真实说过的话】"])
        for question, answer in persona.exemplars:
            lines.append(f"对方：{question}")
            lines.append(f"{name}：{answer}")

    lines.extend(
        [
            "",
            "【规则】",
            "1. 只用微信聊天的口吻：口语、简短，默认 1~2 句；对方追问细节时才展开。",
            "2. 不要客服腔、不要「很高兴为您服务」、不要分点罗列、不要总结。",
            "3. 不确定就反问或说「我想想」，不要编造事实、不要假装知道。",
            "4. 不要提到自己是模型或正在扮演，不要解释规则。",
            "5. 对方发表情或短句时，回同样短的内容，不要强行展开话题。",
        ]
    )
    if extra_system.strip():
        lines.extend(["", "【额外要求】", extra_system.strip()])
    return "\n".join(lines)


def save_persona(settings: Settings, persona: Persona) -> Path:
    path = settings.data_dir / "bot" / "persona.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(persona.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_persona(settings: Settings) -> Persona:
    path = settings.data_dir / "bot" / "persona.json"
    if not path.is_file():
        return Persona()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Persona()
    if not isinstance(data, dict):
        return Persona()
    persona = Persona.from_dict(data)
    persona.particles = [tuple(item) for item in persona.particles]  # type: ignore[misc]
    persona.phrases = [tuple(item) for item in persona.phrases]  # type: ignore[misc]
    persona.exemplars = [tuple(item) for item in persona.exemplars]  # type: ignore[misc]
    return persona


def ensure_persona(settings: Settings, *, force: bool = False) -> Persona:
    persona = load_persona(settings)
    if force or not persona.prompt:
        persona = build_persona(settings)
        if persona.message_count:
            save_persona(settings, persona)
    return persona

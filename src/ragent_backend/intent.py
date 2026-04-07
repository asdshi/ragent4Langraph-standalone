from __future__ import annotations

import re
from typing import List

from src.ragent_backend.schemas import IntentResult


def rewrite_query(query: str) -> str:
    return " ".join(query.split())


def detect_intent(query: str, has_history: bool) -> IntentResult:
    rewritten = rewrite_query(query)

    pronoun_like = any(token in rewritten for token in ["它", "这个", "那个", "that", "it"])
    if pronoun_like and not has_history:
        return IntentResult(
            rewritten_query=rewritten,
            confidence=0.35,
            need_clarify=True,
            clarify_prompt="请补充你指代的对象，例如具体产品名、文档名或业务指标。",
        )

    return IntentResult(
        rewritten_query=rewritten,
        confidence=0.92,
        need_clarify=False,
        clarify_prompt=None,
    )


def split_parallel_subqueries(query: str) -> List[str]:
    """Split a question containing parallel subjects into sub-questions.

    Heuristic strategy:
    - First split by strong separators like "；"/";"/"，并".
    - Then split by conjunctions such as "和/与/以及/及/and".
    - Deduplicate and keep original order.
    """
    normalized = rewrite_query(query).strip()
    if not normalized:
        return []

    # Stage 1: strong sentence-level separators.
    segments = re.split(r"[；;]|，并|,\s*and\s+", normalized)

    # Stage 2: conjunction-based subject split.
    pieces: List[str] = []
    for seg in segments:
        seg = seg.strip(" ,，。？！?！")
        if not seg:
            continue
        sub_parts = re.split(r"\s*(?:和|与|以及|及|and)\s*", seg)
        if len(sub_parts) <= 1:
            pieces.append(seg)
            continue

        # If a predicate exists in the last part, append it to former subjects.
        tail = sub_parts[-1].strip()
        predicate_match = re.search(r"(是.*|有.*|怎么.*|如何.*|多少.*|哪些.*|是什么.*)$", tail)
        predicate = predicate_match.group(1) if predicate_match else ""

        for idx, part in enumerate(sub_parts):
            part = part.strip(" ,，。？！?！")
            if not part:
                continue
            if idx < len(sub_parts) - 1 and predicate:
                pieces.append(f"{part}{predicate}")
            else:
                pieces.append(part)

    if len(pieces) <= 1:
        return [normalized]

    deduped: List[str] = []
    seen = set()
    for piece in pieces:
        item = piece.strip(" ,，。？！?！")
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)

    return deduped or [normalized]

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PlaceEntry:
    name: str
    aliases: tuple[str, ...]
    region: Optional[str]


PLACES_JSON_PATH = Path(__file__).with_name("places.json")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@lru_cache(maxsize=1)
def load_places() -> tuple[PlaceEntry, ...]:
    raw_items = json.loads(PLACES_JSON_PATH.read_text(encoding="utf-8"))
    return tuple(
        PlaceEntry(
            name=item["name"],
            aliases=tuple(item["aliases"]),
            region=item.get("region"),
        )
        for item in raw_items
    )


def match_place(*texts: str) -> tuple[Optional[str], Optional[str], str]:
    normalized_texts = [normalize_text(text) for text in texts if normalize_text(text)]
    if not normalized_texts:
        return None, None, "장소 사전 매칭용 텍스트가 비어 있음"

    best_match: tuple[int, PlaceEntry, str] | None = None
    for entry in load_places():
        for alias in entry.aliases:
            normalized_alias = normalize_text(alias)
            if not normalized_alias:
                continue
            for text in normalized_texts:
                if normalized_alias in text:
                    candidate = (len(normalized_alias), entry, alias)
                    if best_match is None or candidate[0] > best_match[0]:
                        best_match = candidate

    if best_match is None:
        return None, None, "장소 사전에서 일치 항목을 찾지 못함"

    _, entry, alias = best_match
    return entry.name, entry.region, f"장소 사전에서 '{alias}' 별칭으로 '{entry.name}' 매칭"

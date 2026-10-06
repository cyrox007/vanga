from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class ExpertIngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSegment:
    locator: str
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None


_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[\.,](?P<ms>\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _timestamp_seconds(value: str) -> float:
    match = _TIMESTAMP_RE.search(value)
    if not match:
        raise ExpertIngestError(f"Не удалось разобрать VTT timestamp: {value!r}")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000.0
    )


def _format_locator(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_vtt(text: str) -> list[SourceSegment]:
    """Разбирает VTT без сохранения полного транскрипта в registry.

    Повторяющиеся auto-caption cues дедуплицируются по соседнему тексту.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[SourceSegment] = []
    index = 0
    previous_text = ""
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = [part.strip().split(" ", 1)[0] for part in line.split("-->", 1)]
        start = _timestamp_seconds(left)
        end = _timestamp_seconds(right)
        index += 1
        payload: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = html.unescape(_TAG_RE.sub("", lines[index])).strip()
            if cleaned:
                payload.append(cleaned)
            index += 1
        cue_text = _SPACE_RE.sub(" ", " ".join(payload)).strip()
        if cue_text and cue_text != previous_text:
            result.append(
                SourceSegment(
                    locator=_format_locator(start),
                    text=cue_text,
                    start_seconds=start,
                    end_seconds=end,
                )
            )
            previous_text = cue_text
        index += 1
    return result


def _youtube_subtitle_segments(url: str) -> list[SourceSegment]:
    with tempfile.TemporaryDirectory(prefix="vanga-expert-youtube-") as tmp:
        target = Path(tmp) / "source"
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "ru.*,ru,en.*,en",
            "--sub-format",
            "vtt",
            "--no-playlist",
            "--output",
            str(target),
            url,
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "неизвестная ошибка").strip()
            raise ExpertIngestError(f"yt-dlp не смог получить субтитры: {message[-1200:]}")

        files = sorted(Path(tmp).glob("source*.vtt"))
        if not files:
            raise ExpertIngestError("Для YouTube-материала не найдены доступные ru/en субтитры")

        def priority(path: Path) -> tuple[int, str]:
            name = path.name.casefold()
            if ".ru" in name:
                return (0, name)
            if ".en" in name:
                return (1, name)
            return (2, name)

        chosen = sorted(files, key=priority)[0]
        segments = parse_vtt(chosen.read_text(encoding="utf-8", errors="replace"))
        if not segments:
            raise ExpertIngestError("Полученный VTT не содержит текстовых сегментов")
        return segments


def _webpage_segments(url: str) -> list[SourceSegment]:
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Vanga-ExpertCorpus/1.0 (+research annotation)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        node.decompose()

    result: list[SourceSegment] = []
    seen: set[str] = set()
    for node in soup.select("article p, main p, p, article li, main li"):
        value = _SPACE_RE.sub(" ", node.get_text(" ", strip=True)).strip()
        if len(value) < 40 or value in seen:
            continue
        seen.add(value)
        result.append(SourceSegment(locator=f"абзац {len(result) + 1}", text=value))
    if not result:
        raise ExpertIngestError("На странице не найден пригодный текст материала")
    return result


def collect_source_segments(url: str) -> tuple[str, list[SourceSegment]]:
    host = (urlparse(url).hostname or "").casefold()
    if host == "youtu.be" or host.endswith("youtube.com"):
        return "youtube_subtitles", _youtube_subtitle_segments(url)
    return "webpage", _webpage_segments(url)


_DIMENSION_MARKERS: dict[str, tuple[str, ...]] = {
    "motivation": ("мотивац", "зачем", "почему он", "почему она", "motivat", "why does"),
    "characters": ("персонаж", "герой", "героин", "character"),
    "plot": ("сюжет", "сценар", "истори", "plot", "story", "script"),
    "causal_coherence": ("логик", "причин", "следств", "необъяс", "logic", "because", "cause"),
    "worldbuilding": ("мир", "правил", "лор", "worldbuilding", "world rule", "lore"),
    "tone": ("тон", "юмор", "атмосфер", "tone", "humor", "mood"),
    "ending": ("финал", "концов", "ending", "finale"),
    "relationships": ("отношен", "любов", "дружб", "relationship"),
    "continuity": ("нестыков", "противореч", "continuity", "inconsisten"),
}

_CANDIDATE_MARKERS = (
    "потому",
    "поэтому",
    "из-за",
    "не работает",
    "непонят",
    "нелог",
    "проблем",
    "ошиб",
    "провал",
    "лишн",
    "не объяс",
    "не показ",
    "не подготов",
    "противореч",
    "because",
    "therefore",
    "doesn't work",
    "does not work",
    "problem",
    "inconsistent",
    "unexplained",
)


def _dimension_for(text: str) -> str:
    lowered = text.casefold()
    ranked: list[tuple[int, str]] = []
    for dimension, markers in _DIMENSION_MARKERS.items():
        score = sum(1 for marker in markers if marker in lowered)
        if score:
            ranked.append((score, dimension))
    if not ranked:
        return "script_logic"
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked[0][1]


def candidate_segments(
    segments: list[SourceSegment], *, max_candidates: int = 20
) -> list[SourceSegment]:
    """Отбирает потенциально аналитические фрагменты без объявления их gold-разметкой."""
    scored: list[tuple[int, int, SourceSegment]] = []
    for index, segment in enumerate(segments):
        text = segment.text.strip()
        if len(text) < 35:
            continue
        lowered = text.casefold()
        marker_score = sum(1 for marker in _CANDIDATE_MARKERS if marker in lowered)
        punctuation_score = int("?" in text) + int("!" in text)
        structural_score = int(_dimension_for(text) != "script_logic")
        score = marker_score * 3 + structural_score + punctuation_score
        if score >= 2:
            scored.append((score, index, segment))
    scored.sort(key=lambda row: (-row[0], row[1]))
    selected = sorted(scored[: max(1, max_candidates)], key=lambda row: row[1])
    return [row[2] for row in selected]


def build_annotation_draft(
    *,
    case_id: str,
    material_id: str,
    source_url: str,
    segments: list[SourceSegment],
    max_candidates: int = 20,
) -> dict:
    candidates = candidate_segments(segments, max_candidates=max_candidates)
    claims: list[dict] = []
    evidence: list[dict] = []
    for number, segment in enumerate(candidates, start=1):
        claim_id = f"{case_id}-auto-{number:03d}"
        excerpt = _SPACE_RE.sub(" ", segment.text).strip()[:420]
        claims.append(
            {
                "claim_id": claim_id,
                "case_id": case_id,
                "material_id": material_id,
                "dimension": _dimension_for(excerpt),
                "change_type": "unknown",
                "timecode_or_section": segment.locator,
                "claim_summary": f"ПРОВЕРИТЬ: {excerpt[:280]}",
                "observation": "ПРОВЕРИТЬ: сформулируйте наблюдаемый факт без экспертной оценки",
                "structural_consequence": "ПРОВЕРИТЬ: укажите структурное следствие наблюдаемого факта",
                "expert_interpretation": f"ПРОВЕРИТЬ: экспертский фрагмент у {segment.locator}: {excerpt[:260]}",
                "confidence": 0.5,
                "tags": ["auto_draft"],
            }
        )
        evidence.append(
            {
                "evidence_id": f"{claim_id}-support-001",
                "claim_id": claim_id,
                "polarity": "supporting",
                "evidence_kind": "material_reference",
                "description": f"ПРОВЕРИТЬ: короткий фрагмент исходного материала: {excerpt[:240]}",
                "locator": segment.locator,
                "reference_id": None,
                "confidence": 0.5,
            }
        )
    return {
        "meta": {
            "case_id": case_id,
            "material_id": material_id,
            "source_url": source_url,
            "generated": True,
            "requires_human_review": True,
            "segments_seen": len(segments),
            "candidates": len(candidates),
            "instruction": "Все поля с префиксом «ПРОВЕРИТЬ:» необходимо подтвердить или исправить до validate/apply.",
        },
        "claims": claims,
        "evidence": evidence,
    }

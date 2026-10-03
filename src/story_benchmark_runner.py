from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.adaptation_analysis import AdaptationValidationError
from src.story_alignment import story_map_from_payload
from src.story_benchmark import AlignmentGoldCase, StoryAlignmentBenchmark
from src.story_benchmark_suite import (
    BenchmarkSuiteManifest,
    StoryBenchmarkSuiteAggregator,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AdaptationValidationError(f"Некорректный JSON {path}: {exc}") from exc


class StoryBenchmarkSuiteRunner:
    """Запускает benchmark manifest без хранения/копирования исходных текстов."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        if not self.base_dir.is_dir():
            raise AdaptationValidationError(
                f"Benchmark base_dir не существует: {self.base_dir}"
            )

    def _resolve(self, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute():
            raise AdaptationValidationError(
                "Benchmark manifest должен использовать только относительные пути"
            )
        candidate = (self.base_dir / relative).resolve()
        try:
            candidate.relative_to(self.base_dir)
        except ValueError as exc:
            raise AdaptationValidationError(
                f"Benchmark path выходит за base_dir: {value}"
            ) from exc
        if not candidate.is_file():
            raise AdaptationValidationError(
                f"Benchmark input не найден: {value}"
            )
        return candidate

    @staticmethod
    def _validate_predicted_alignment(source, adaptation, predicted) -> None:
        source_by_key = {node.key: node for node in source.nodes}
        adaptation_by_key = {node.key: node for node in adaptation.nodes}
        for node in predicted.nodes:
            if not node.maps_from:
                continue
            adaptation_node = adaptation_by_key.get(node.key)
            if adaptation_node is None:
                raise AdaptationValidationError(
                    "Predicted alignment содержит maps_from у node, отсутствующего "
                    f"в исходной adaptation map: {node.key}"
                )
            if adaptation_node.kind != node.kind:
                raise AdaptationValidationError(
                    f"Predicted node {node.key} изменил kind относительно adaptation map"
                )
            for source_key in node.maps_from:
                source_node = source_by_key.get(source_key)
                if source_node is None:
                    raise AdaptationValidationError(
                        f"Predicted maps_from ссылается на неизвестный source key {source_key}"
                    )
                if source_node.kind != adaptation_node.kind:
                    raise AdaptationValidationError(
                        "Predicted maps_from связывает разные StoryNode kind: "
                        f"{source_key} -> {node.key}"
                    )

    def run(
        self,
        manifest_payload: dict[str, Any],
        *,
        only_split: str | None = None,
    ) -> dict[str, Any]:
        manifest = BenchmarkSuiteManifest.from_dict(manifest_payload)
        selected = [
            row for row in manifest.cases
            if only_split is None or row.split == str(only_split).strip().casefold()
        ]
        if not selected:
            raise AdaptationValidationError(
                f"В suite {manifest.suite_id} нет cases для split={only_split}"
            )

        case_results: list[dict[str, Any]] = []
        input_hashes: list[dict[str, str]] = []
        for item in selected:
            source_path = self._resolve(item.source)
            adaptation_path = self._resolve(item.adaptation)
            gold_path = self._resolve(item.gold)
            predicted_path = self._resolve(item.predicted)

            source = story_map_from_payload(_load_json(source_path))
            adaptation = story_map_from_payload(_load_json(adaptation_path))
            gold_payload = _load_json(gold_path)
            predicted = story_map_from_payload(_load_json(predicted_path))

            gold_case = AlignmentGoldCase.from_dict(
                gold_payload,
                source=source,
                adaptation=adaptation,
            )
            if gold_case.case_id != item.case_id:
                raise AdaptationValidationError(
                    f"Manifest case_id={item.case_id} не совпадает с gold case_id={gold_case.case_id}"
                )
            if gold_case.split != item.split:
                raise AdaptationValidationError(
                    f"Manifest split={item.split} не совпадает с gold split={gold_case.split}"
                )

            self._validate_predicted_alignment(source, adaptation, predicted)
            result = StoryAlignmentBenchmark.evaluate(
                source=source,
                adaptation=adaptation,
                gold=gold_case,
                predicted_map=predicted,
            )
            case_results.append(result)

            for role, path, manifest_value in (
                ("source", source_path, item.source),
                ("adaptation", adaptation_path, item.adaptation),
                ("gold", gold_path, item.gold),
                ("predicted", predicted_path, item.predicted),
            ):
                input_hashes.append(
                    {
                        "case_id": item.case_id,
                        "role": role,
                        "path": manifest_value,
                        "sha256": _sha256_file(path),
                    }
                )

        fingerprint_payload = {
            "manifest_fingerprint": manifest.fingerprint(),
            "inputs": sorted(
                input_hashes,
                key=lambda row: (row["case_id"], row["role"], row["path"]),
            ),
        }
        content_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        report = StoryBenchmarkSuiteAggregator.aggregate(
            case_results,
            suite_id=manifest.suite_id,
            suite_fingerprint=content_fingerprint,
            only_split=only_split,
        )
        report["manifest_fingerprint_sha256"] = manifest.fingerprint()
        report["input_hashes"] = fingerprint_payload["inputs"]
        report["case_results"] = case_results
        return report

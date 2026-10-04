from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from settings import config


@dataclass(frozen=True)
class ModelRuntimeDescriptor:
    generation: str
    model_path: Path
    metadata_path: Path
    source: str


def resolve_model_runtime_descriptor() -> ModelRuntimeDescriptor:
    """Однократно читает активный model pointer и возвращает согласованный descriptor.

    Один вызов не должен смешивать путь старого поколения с идентификатором нового.
    Для legacy-инсталляций сохраняется fallback на ``models/model.cbm``.
    """
    root = Path(config.ABSPATH) / "models"
    pointer = root / "current.json"
    pointer_error: Exception | None = None

    if pointer.exists():
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            generation = str(payload["generation"]).strip()
            if not generation:
                raise ValueError("generation пуст")
            release_dir = root / "releases" / generation
            model_path = release_dir / "model.cbm"
            metadata_path = release_dir / "metadata.pkl"
            if not model_path.is_file() or not metadata_path.is_file():
                raise FileNotFoundError(
                    f"Артефакты generation={generation} отсутствуют или неполны"
                )
            return ModelRuntimeDescriptor(
                generation=generation,
                model_path=model_path,
                metadata_path=metadata_path,
                source="pointer",
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            pointer_error = exc

    legacy_model = root / "model.cbm"
    legacy_metadata = root / "metadata.pkl"
    if legacy_model.is_file() and legacy_metadata.is_file():
        return ModelRuntimeDescriptor(
            generation=f"legacy:{legacy_model.stat().st_mtime_ns}",
            model_path=legacy_model,
            metadata_path=legacy_metadata,
            source="legacy",
        )

    if pointer_error is not None:
        raise FileNotFoundError(
            f"Активная модель Vanga недоступна: {pointer_error}"
        ) from pointer_error
    raise FileNotFoundError("Активная модель Vanga не найдена")

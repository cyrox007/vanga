from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import requests

from settings import config
from src.logger import setup_logger


logger = setup_logger(__name__)


def _metadata_path(local_path: Path) -> Path:
    return local_path.with_suffix(local_path.suffix + ".meta.json")


def _read_metadata(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_metadata(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _remote_signature(response: requests.Response) -> dict:
    return {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "content_length": response.headers.get("Content-Length"),
    }


def _same_signature(local: dict, remote: dict) -> bool:
    comparable = ("etag", "last_modified", "content_length")
    seen = False
    for key in comparable:
        remote_value = remote.get(key)
        if remote_value:
            seen = True
            if local.get(key) != remote_value:
                return False
    return seen


def _validate_gzip(path: Path) -> None:
    if path.stat().st_size == 0:
        raise RuntimeError(f"Скачан пустой файл: {path}")
    with gzip.open(path, "rb") as handle:
        handle.read(1024)


def download_imdb_dataset(dataset_name: str, *, force: bool = False) -> bool:
    """
    Обновляет один официальный IMDb dataset.

    Возвращает True, если локальный файл был заменён новой версией.
    При временной сетевой ошибке существующий валидный файл сохраняется.
    """
    url = f"https://datasets.imdbws.com/{dataset_name}.tsv.gz"
    data_dir = Path(config.ABSPATH) / "data" / "imdb"
    data_dir.mkdir(parents=True, exist_ok=True)

    local_path = data_dir / f"{dataset_name}.tsv.gz"
    metadata_path = _metadata_path(local_path)
    old_metadata = _read_metadata(metadata_path)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "KinoVanga/0.1 (+https://github.com/cyrox007/vanga)",
            "Accept-Encoding": "identity",
        }
    )

    remote_signature: dict = {}
    if not force:
        try:
            head = session.head(url, timeout=30, allow_redirects=True)
            head.raise_for_status()
            remote_signature = _remote_signature(head)
            if (
                local_path.exists()
                and _same_signature(old_metadata, remote_signature)
            ):
                logger.info(f"{dataset_name}: источник не изменился")
                return False
        except requests.RequestException as exc:
            if local_path.exists():
                logger.warning(
                    f"{dataset_name}: не удалось проверить обновление ({exc}); "
                    "используем существующий файл"
                )
                return False
            raise RuntimeError(
                f"{dataset_name}: нет локального файла и источник недоступен"
            ) from exc

    logger.info(f"{dataset_name}: скачивание свежего набора IMDb")
    temp_path = local_path.with_suffix(local_path.suffix + ".part")
    temp_path.unlink(missing_ok=True)

    try:
        with session.get(
            url,
            timeout=(30, 300),
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            if not remote_signature:
                remote_signature = _remote_signature(response)

            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)

        _validate_gzip(temp_path)
        os.replace(temp_path, local_path)
        _write_metadata(
            metadata_path,
            {
                **remote_signature,
                "url": url,
                "size": local_path.stat().st_size,
            },
        )
        logger.info(
            f"{dataset_name}: обновлён, размер={local_path.stat().st_size} байт"
        )
        return True
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

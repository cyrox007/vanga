from __future__ import annotations

import json
import pickle
import platform
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from settings import config


@dataclass(frozen=True)
class ReadinessCheck:
    """Один воспроизводимый пункт проверки готовности к релизу."""

    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None


class ReleaseReadiness:
    """Проверяет локальный runtime и, опционально, живой HTTP API Vanga.

    Проверка специально не загружает CatBoost-модель и не обращается к внешним
    источникам данных. Live-проверки обращаются только к указанному локальному
    Vanga API.
    """

    def __init__(
        self,
        *,
        imdb_db_path: str | Path | None = None,
        future_db_path: str | Path | None = None,
        root: str | Path | None = None,
        strict_p9: bool = False,
    ) -> None:
        self.root = Path(root or config.ABSPATH)
        self.imdb_db_path = Path(imdb_db_path or config.IMDB_DB_PATH)
        self.future_db_path = Path(future_db_path or config.FUTURE_RELEASE_DB_PATH)
        self.strict_p9 = bool(strict_p9)

    @staticmethod
    def _check(name: str, status: str, message: str, **details: Any) -> ReadinessCheck:
        return ReadinessCheck(
            name=name,
            status=status,
            message=message,
            details=details or None,
        )

    @staticmethod
    def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
        rows = conn.execute("SHOW TABLES").fetchall()
        return {str(row[0]) for row in rows}

    def check_python(self) -> ReadinessCheck:
        version = platform.python_version()
        major, minor = platform.python_version_tuple()[:2]
        supported = (int(major), int(minor)) >= (3, 10)
        return self._check(
            "python",
            "pass" if supported else "fail",
            (
                f"Python {version} поддерживается"
                if supported
                else f"Python {version} слишком старый; требуется Python 3.10+"
            ),
            version=version,
        )

    def check_required_files(self) -> ReadinessCheck:
        required = (
            "api.py",
            "settings.py",
            "src/future_http_api.py",
            "src/future_regional_release.py",
            "deploy/update-vanga.sh",
        )
        missing = [name for name in required if not (self.root / name).is_file()]
        if missing:
            return self._check(
                "required_files",
                "fail",
                "Не хватает обязательных файлов runtime",
                missing=missing,
            )
        return self._check(
            "required_files",
            "pass",
            "Обязательные runtime-файлы присутствуют",
            count=len(required),
        )

    def check_imdb_db(self) -> ReadinessCheck:
        path = self.imdb_db_path
        if not path.is_file():
            return self._check(
                "imdb_db",
                "fail",
                f"IMDb DuckDB не найден: {path}",
                path=str(path),
            )
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                tables = self._table_names(conn)
                required_any = {"titles", "title_basics"}
                has_titles = bool(tables & required_any)
                has_crew = "title_crew" in tables or "title_writers" in tables
                if not has_titles:
                    return self._check(
                        "imdb_db",
                        "fail",
                        "IMDb DuckDB открывается, но таблица фильмов не найдена",
                        path=str(path),
                        tables=sorted(tables),
                    )
                status = "pass" if has_crew else "warn"
                message = (
                    "IMDb DuckDB готов, crew/writer слой найден"
                    if has_crew
                    else "IMDb DuckDB готов, но crew/writer слой не подтверждён"
                )
                return self._check(
                    "imdb_db",
                    status,
                    message,
                    path=str(path),
                    size_bytes=path.stat().st_size,
                    crew_or_writer_table=has_crew,
                )
            finally:
                conn.close()
        except Exception as exc:
            return self._check(
                "imdb_db",
                "fail",
                f"IMDb DuckDB не открывается: {exc}",
                path=str(path),
            )

    def check_active_model(self) -> ReadinessCheck:
        model_root = self.root / "models"
        pointer = model_root / "current.json"
        model_path: Path | None = None
        generation = "legacy"

        if pointer.is_file():
            try:
                payload = json.loads(pointer.read_text(encoding="utf-8"))
                generation = str(payload.get("generation") or "").strip()
                if not generation:
                    raise ValueError("generation отсутствует")
                model_path = model_root / "releases" / generation / "model.cbm"
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                return self._check(
                    "active_model",
                    "fail",
                    f"Некорректный models/current.json: {exc}",
                    path=str(pointer),
                )
        else:
            legacy = model_root / "model.cbm"
            if legacy.is_file():
                model_path = legacy

        if model_path is None or not model_path.is_file():
            return self._check(
                "active_model",
                "fail",
                "Активная CatBoost-модель не найдена",
                generation=generation,
            )

        metadata_path = model_path.parent / "metadata.pkl"
        if not metadata_path.is_file():
            return self._check(
                "active_model",
                "fail",
                "У активной модели отсутствует metadata.pkl",
                generation=generation,
                model_path=str(model_path),
            )

        try:
            with metadata_path.open("rb") as handle:
                metadata = pickle.load(handle)
        except Exception as exc:
            return self._check(
                "active_model",
                "fail",
                f"metadata.pkl активной модели не читается: {exc}",
                generation=generation,
            )

        metadata = metadata if isinstance(metadata, dict) else {}
        gate = metadata.get("quality_gate")
        gate_passed = None
        if isinstance(gate, dict):
            gate_passed = gate.get("passed")
        status = "fail" if gate_passed is False else "pass"
        message = (
            "Активная модель найдена, quality gate не блокирует публикацию"
            if status == "pass"
            else "Активная модель помечена как не прошедшая quality gate"
        )
        return self._check(
            "active_model",
            status,
            message,
            generation=generation,
            model_path=str(model_path),
            schema_version=metadata.get("schema_version"),
            test_mae=metadata.get("test_mae"),
            quality_gate_passed=gate_passed,
        )

    def check_p9_registry(self) -> ReadinessCheck:
        path = self.future_db_path
        if not path.is_file():
            status = "fail" if self.strict_p9 else "warn"
            return self._check(
                "p9_registry",
                status,
                f"P9 registry ещё не создан: {path}",
                path=str(path),
            )
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                tables = self._table_names(conn)
                projects_table = "future_release_projects"
                if projects_table not in tables:
                    return self._check(
                        "p9_registry",
                        "fail",
                        "P9 DuckDB открывается, но registry schema отсутствует",
                        tables=sorted(tables),
                    )

                counts: dict[str, int] = {}
                for key, table in (
                    ("projects", "future_release_projects"),
                    ("sources", "future_release_sources"),
                    ("release_windows", "future_release_windows"),
                    ("temporal_facts", "future_temporal_facts"),
                    ("territory_mappings", "future_territory_mappings"),
                ):
                    if table in tables:
                        counts[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    else:
                        counts[key] = 0

                project_count = counts["projects"]
                status = "pass" if project_count > 0 else ("fail" if self.strict_p9 else "warn")
                message = (
                    f"P9 registry готов: проектов {project_count}"
                    if project_count > 0
                    else "P9 registry инициализирован, но каталог пока пуст"
                )
                return self._check(
                    "p9_registry",
                    status,
                    message,
                    path=str(path),
                    **counts,
                )
            finally:
                conn.close()
        except Exception as exc:
            return self._check(
                "p9_registry",
                "fail",
                f"P9 registry не открывается: {exc}",
                path=str(path),
            )

    @staticmethod
    def _http_json(url: str, *, timeout: float = 4.0) -> tuple[int, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                return int(response.status), body
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                body = {"error": f"HTTP {exc.code}"}
            return int(exc.code), body

    def check_live_api(
        self,
        *,
        api_base_url: str,
        markets: Iterable[str],
    ) -> list[ReadinessCheck]:
        base = api_base_url.rstrip("/")
        checks: list[ReadinessCheck] = []
        for path, name in (("/health", "api_health"), ("/model-info", "api_model_info")):
            try:
                status_code, payload = self._http_json(base + path)
                ok = status_code == 200 and isinstance(payload, dict) and bool(payload.get("ok"))
                checks.append(
                    self._check(
                        name,
                        "pass" if ok else "fail",
                        f"{path}: {'OK' if ok else 'не готов'}",
                        http_status=status_code,
                    )
                )
            except Exception as exc:
                checks.append(self._check(name, "fail", f"{path} недоступен: {exc}"))

        now = datetime.now(timezone.utc).isoformat()
        for raw_market in markets:
            market = str(raw_market).strip().upper()
            if not market:
                continue
            query = urllib.parse.urlencode(
                {
                    "cutoff": now,
                    "from_at": now,
                    "territory": market,
                    "include_conflicts": "true",
                }
            )
            name = f"future_catalog_{market.lower()}"
            try:
                status_code, payload = self._http_json(f"{base}/future/catalog?{query}")
                ok = status_code == 200 and isinstance(payload, dict) and bool(payload.get("ok"))
                items = payload.get("items") if isinstance(payload, dict) else None
                count = len(items) if isinstance(items, list) else 0
                if not ok:
                    checks.append(
                        self._check(
                            name,
                            "fail",
                            f"Future catalog {market} не готов",
                            http_status=status_code,
                        )
                    )
                else:
                    status = "pass" if count > 0 else ("fail" if self.strict_p9 else "warn")
                    checks.append(
                        self._check(
                            name,
                            status,
                            f"Future catalog {market}: {count} проектов",
                            http_status=status_code,
                            items=count,
                        )
                    )
            except Exception as exc:
                checks.append(self._check(name, "fail", f"Future catalog {market} недоступен: {exc}"))
        return checks

    def run(
        self,
        *,
        live_api: bool = False,
        api_base_url: str = "http://127.0.0.1:9100",
        markets: Iterable[str] = ("DE", "US", "NL"),
    ) -> dict[str, Any]:
        checks = [
            self.check_python(),
            self.check_required_files(),
            self.check_imdb_db(),
            self.check_active_model(),
            self.check_p9_registry(),
        ]
        if live_api:
            checks.extend(self.check_live_api(api_base_url=api_base_url, markets=markets))

        blockers = [item for item in checks if item.status == "fail"]
        warnings = [item for item in checks if item.status == "warn"]
        return {
            "version": 1,
            "ready": not blockers,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": [asdict(item) for item in checks],
            "summary": {
                "pass": sum(item.status == "pass" for item in checks),
                "warn": len(warnings),
                "fail": len(blockers),
            },
        }

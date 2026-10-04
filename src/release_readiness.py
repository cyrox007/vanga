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
    """Проверяет runtime без загрузки второй CatBoost-модели.

    Offline-проход не использует сеть. Live-проход обращается только к явно
    указанному локальному Vanga API и не запускает collectors.
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
        return ReadinessCheck(name, status, message, details or None)

    @staticmethod
    def _tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
        return {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}

    def check_python(self) -> ReadinessCheck:
        version = platform.python_version()
        major, minor = (int(part) for part in platform.python_version_tuple()[:2])
        ok = (major, minor) >= (3, 10)
        return self._check(
            "python",
            "pass" if ok else "fail",
            f"Python {version} {'поддерживается' if ok else 'слишком старый; требуется 3.10+'}",
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
        missing = [item for item in required if not (self.root / item).is_file()]
        if missing:
            return self._check(
                "required_files",
                "fail",
                "Не хватает обязательных runtime-файлов",
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
            return self._check("imdb_db", "fail", f"IMDb DuckDB не найден: {path}")
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                tables = self._tables(conn)
            finally:
                conn.close()
        except Exception as exc:
            return self._check("imdb_db", "fail", f"IMDb DuckDB не открывается: {exc}")

        if "title_basics" not in tables:
            return self._check(
                "imdb_db",
                "fail",
                "IMDb DuckDB не содержит title_basics",
                tables=sorted(tables),
            )
        has_crew = "title_crew" in tables and "title_writers" in tables
        return self._check(
            "imdb_db",
            "pass" if has_crew else "warn",
            (
                "IMDb DuckDB готов, title_crew/title_writers найдены"
                if has_crew
                else "IMDb DuckDB открыт, но title_crew/title_writers ещё не подтверждены"
            ),
            path=str(path),
            size_bytes=path.stat().st_size,
            crew_writer_ready=has_crew,
        )

    def check_active_model(self) -> ReadinessCheck:
        model_root = self.root / "models"
        pointer = model_root / "current.json"
        generation = "legacy"
        model_path: Path | None = None

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
            )
        try:
            with metadata_path.open("rb") as handle:
                metadata = pickle.load(handle)
        except Exception as exc:
            return self._check(
                "active_model",
                "fail",
                f"metadata.pkl не читается: {exc}",
                generation=generation,
            )

        metadata = metadata if isinstance(metadata, dict) else {}
        gate = metadata.get("quality_gate")
        gate_passed = gate.get("passed") if isinstance(gate, dict) else None
        if gate_passed is False:
            return self._check(
                "active_model",
                "fail",
                "Активная generation помечена как не прошедшая quality gate",
                generation=generation,
                schema_version=metadata.get("schema_version"),
                test_mae=metadata.get("test_mae"),
            )
        return self._check(
            "active_model",
            "pass",
            "Активная модель и metadata найдены; quality gate не блокирует runtime",
            generation=generation,
            schema_version=metadata.get("schema_version"),
            test_mae=metadata.get("test_mae"),
            quality_gate_passed=gate_passed,
        )

    def check_p9_registry(self) -> ReadinessCheck:
        path = self.future_db_path
        if not path.is_file():
            return self._check(
                "p9_registry",
                "fail" if self.strict_p9 else "warn",
                f"P9 registry ещё не создан: {path}",
            )
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                tables = self._tables(conn)
                if "future_release_projects" not in tables:
                    return self._check(
                        "p9_registry",
                        "fail",
                        "P9 DuckDB открыт, но registry schema отсутствует",
                        tables=sorted(tables),
                    )
                table_map = {
                    "projects": "future_release_projects",
                    "sources": "future_release_sources",
                    "release_windows": "future_release_windows",
                    "temporal_facts": "future_release_temporal_facts",
                    "territory_mappings": "future_release_territory_mappings",
                }
                counts = {
                    key: (
                        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                        if table in tables
                        else 0
                    )
                    for key, table in table_map.items()
                }
            finally:
                conn.close()
        except Exception as exc:
            return self._check("p9_registry", "fail", f"P9 registry не открывается: {exc}")

        projects = counts["projects"]
        status = "pass" if projects else ("fail" if self.strict_p9 else "warn")
        return self._check(
            "p9_registry",
            status,
            f"P9 registry готов: проектов {projects}" if projects else "P9 registry инициализирован, но каталог пуст",
            path=str(path),
            **counts,
        )

    @staticmethod
    def _http_json(url: str, *, timeout: float = 4.0) -> tuple[int, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
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
        result: list[ReadinessCheck] = []
        for path, name in (("/health", "api_health"), ("/model-info", "api_model_info")):
            try:
                status, body = self._http_json(base + path)
                ok = status == 200 and isinstance(body, dict) and bool(body.get("ok"))
                result.append(
                    self._check(
                        name,
                        "pass" if ok else "fail",
                        f"{path}: {'OK' if ok else 'не готов'}",
                        http_status=status,
                    )
                )
            except Exception as exc:
                result.append(self._check(name, "fail", f"{path} недоступен: {exc}"))

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
                status, body = self._http_json(f"{base}/future/catalog?{query}")
                ok = status == 200 and isinstance(body, dict) and bool(body.get("ok"))
                items = body.get("items") if isinstance(body, dict) else None
                count = len(items) if isinstance(items, list) else 0
                if not ok:
                    result.append(
                        self._check(name, "fail", f"Future catalog {market} не готов", http_status=status)
                    )
                else:
                    result.append(
                        self._check(
                            name,
                            "pass" if count else ("fail" if self.strict_p9 else "warn"),
                            f"Future catalog {market}: {count} проектов",
                            http_status=status,
                            items=count,
                        )
                    )
            except Exception as exc:
                result.append(self._check(name, "fail", f"Future catalog {market} недоступен: {exc}"))
        return result

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
        failures = [item for item in checks if item.status == "fail"]
        warnings = [item for item in checks if item.status == "warn"]
        return {
            "version": 1,
            "ready": not failures,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": [asdict(item) for item in checks],
            "summary": {
                "pass": sum(item.status == "pass" for item in checks),
                "warn": len(warnings),
                "fail": len(failures),
            },
        }

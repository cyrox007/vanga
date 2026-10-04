import os
from pathlib import Path


class Config:
    ABSPATH = os.path.dirname(os.path.abspath(__file__))

    IMDB_DB_PATH = os.getenv(
        "VANGA_IMDB_DB",
        str(Path(ABSPATH) / "imdb.duckdb"),
    )
    # P7 point-in-time история IMDb rating физически отделена от текущего
    # snapshot IMDb. База append-only: прошлые наблюдения не перезаписываются.
    RATING_HISTORY_DB_PATH = os.getenv(
        "VANGA_RATING_HISTORY_DB",
        str(Path(ABSPATH) / "rating_history.duckdb"),
    )
    # P8 хранит только агрегатные pre-release audience signals с timestamp,
    # method/version и provenance. Raw user data/тексты в эту БД не попадают.
    AUDIENCE_SIGNALS_DB_PATH = os.getenv(
        "VANGA_AUDIENCE_SIGNALS_DB",
        str(Path(ABSPATH) / "audience_signals.duckdb"),
    )
    # P9 — локальный cache/registry будущих релизов. Inference не должен
    # обращаться к внешним API: только к уже сохранённым датированным facts.
    FUTURE_RELEASE_DB_PATH = os.getenv(
        "VANGA_FUTURE_RELEASE_DB",
        str(Path(ABSPATH) / "future_releases.duckdb"),
    )
    ENRICHMENT_DB_PATH = os.getenv(
        "VANGA_ENRICHMENT_DB",
        str(Path(ABSPATH) / "enrichment.duckdb"),
    )
    # Ретроспективный анализ адаптаций физически отделён от inference/training.
    # В этой БД могут храниться признаки, извлечённые уже после премьеры, поэтому
    # их нельзя случайно подмешивать в pre-release модель Vanga.
    ADAPTATION_DB_PATH = os.getenv(
        "VANGA_ADAPTATION_DB",
        str(Path(ABSPATH) / "adaptation.duckdb"),
    )
    # Структурированный экспертный корпус хранится отдельно от Adaptation Analyzer
    # и prediction model. Здесь нет полных транскриптов/обзоров: только ссылки,
    # таймкоды/разделы, наши аннотации и provenance к StoryMap/StoryDiff.
    EXPERT_CORPUS_DB_PATH = os.getenv(
        "VANGA_EXPERT_CORPUS_DB",
        str(Path(ABSPATH) / "expert_corpus.duckdb"),
    )
    # P6 registry хранит не production-features, а preregistered гипотезы:
    # retrospective evidence -> только заранее доступные candidate proxies ->
    # обязательный temporal ablation. Физическое отделение не позволяет
    # StoryDiff/expert findings случайно стать входом production-модели.
    PROXY_HYPOTHESES_DB_PATH = os.getenv(
        "VANGA_PROXY_HYPOTHESES_DB",
        str(Path(ABSPATH) / "proxy_hypotheses.duckdb"),
    )
    # Pre-release facts о первоисточнике хранятся отдельно от retrospective
    # Adaptation Analyzer. Здесь допустимы только facts/provenance, доступные
    # наблюдателю на соответствующий cutoff.
    SOURCE_CONTEXT_DB_PATH = os.getenv(
        "VANGA_SOURCE_CONTEXT_DB",
        str(Path(ABSPATH) / "source_context.duckdb"),
    )
    # Production Context хранит только датированные факты с provenance. Он
    # отделён от IMDb и от модели: в training попадут только признаки,
    # рассчитанные as-of конкретной даты и прошедшие отдельный temporal ablation.
    PRODUCTION_CONTEXT_DB_PATH = os.getenv(
        "VANGA_PRODUCTION_CONTEXT_DB",
        str(Path(ABSPATH) / "production_context.duckdb"),
    )
    TRAIN_MIN_FREE_DISK_GB = max(
        1.0,
        float(os.getenv("VANGA_TRAIN_MIN_FREE_DISK_GB", "3")),
    )
    # Жёсткий бюджет размера опубликованной модели. Большая модель может
    # сохраниться успешно, но не поместиться в память inference-сервиса.
    TRAIN_MAX_MODEL_SIZE_MB = max(
        64,
        int(os.getenv("VANGA_TRAIN_MAX_MODEL_SIZE_MB", "512")),
    )
    # Допустимое ухудшение MAE на сопоставимом temporal holdout.
    # Если текущая и candidate-модель проверялись на одном периоде, более
    # сильная регрессия блокирует атомарную публикацию candidate.
    TRAIN_MAX_MAE_REGRESSION = max(
        0.0,
        float(os.getenv("VANGA_TRAIN_MAX_MAE_REGRESSION", "0.03")),
    )
    WIKIMEDIA_USER_AGENT = os.getenv(
        "VANGA_WIKIMEDIA_USER_AGENT",
        "KinoVanga/0.1 (https://github.com/cyrox007/vanga)",
    ).strip()
    WIKIMEDIA_MIN_INTERVAL_SECONDS = max(
        0.2,
        float(os.getenv("VANGA_WIKIMEDIA_MIN_INTERVAL_SECONDS", "0.5")),
    )
    WIKIMEDIA_TIMEOUT_SECONDS = max(
        5,
        int(os.getenv("VANGA_WIKIMEDIA_TIMEOUT_SECONDS", "20")),
    )


config = Config()

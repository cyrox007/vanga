from __future__ import annotations

from pathlib import Path


# При `python scripts/<команда>.py` в sys.path попадает `scripts/`, а корень
# репозитория — нет. Этот пакет-шлюз оставляет документированный способ запуска
# рабочим: импорт `src.*` ищет реальные модули в корневом каталоге `src/`.
ROOT_SRC = Path(__file__).resolve().parents[2] / "src"
__path__ = [str(ROOT_SRC)]

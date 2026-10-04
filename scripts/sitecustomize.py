from __future__ import annotations

import sys
from pathlib import Path


# При запуске `python scripts/<команда>.py` Python добавляет в sys.path каталог
# `scripts/`, но не корень репозитория. Поэтому импорты `src` и `settings`
# ломались в чистом окружении, хотя именно такой запуск указан в документации.
# sitecustomize загружается самим Python до выполнения целевого скрипта и
# добавляет корень проекта один раз для всех CLI-команд.
ROOT = Path(__file__).resolve().parent.parent
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from __future__ import annotations

import importlib.util
from pathlib import Path


# Прямой запуск файла из `scripts/` делает этот каталог первым в sys.path.
# Загружаем настоящий корневой settings.py под внутренним именем и экспортируем
# его публичные атрибуты, не требуя PYTHONPATH и не создавая рекурсивный import.
ROOT_SETTINGS = Path(__file__).resolve().parent.parent / "settings.py"
_spec = importlib.util.spec_from_file_location("_vanga_root_settings", ROOT_SETTINGS)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Не удалось загрузить корневой settings.py: {ROOT_SETTINGS}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

for _name in dir(_module):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_module, _name)

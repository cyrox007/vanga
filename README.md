# 🎬 КиноВанга XD — нейросеть-кинокритик

> "Почему 'Крестный отец' получил 9.2? А 'Титаник' — 7.8?"  
> **КиноВанга** предсказывает рейтинг фильма до его выхода.

## 📌 Описание

Модель анализирует:
- Название и описание сюжета (NLP)
- Год и длительность
- Режиссёра (учитывает его прошлые работы)
- Является ли фильм ремейком

## 🛠️ Использование

```python
from kinovanga import Kinovanga

# Обучение (опционально)
kino = Kinovanga()
kino.train(df)  # ваш DataFrame

# Предсказание
rating = kino.predict_rating(
    title="Дюна",
    director="Дени Вильнёв",
    year=2021,
    runtime=155,
    description="Эпическая космическая сага о борьбе за ресурсы..."
)
print(rating)  # → 8.7

## 🔐 Демонстрация интеграции с jsint-site

Ветка `feature/jsint-site-demo` добавляет минимальный клиент control plane. Он показывает реальный сценарий: установка Vanga получает собственный `installation_id`, активируется лицензией из jsint-site и после этого отправляет heartbeat.

Состояние хранится вне репозитория в `~/.vanga/jsint.json` (путь можно переопределить через `VANGA_JSINT_STATE`).

```bash
# Проверка API
python jsint_demo.py --site https://your-jsint-site.example health

# Активация одноразовым кодом из админки jsint-site
python jsint_demo.py --site https://your-jsint-site.example activate --code <activation-code>

# Проверка связи — после этого установка видна в реестре лицензий как «На связи»
python jsint_demo.py --site https://your-jsint-site.example heartbeat
```

Для активации готовым подписанным токеном вместо кода используйте `--license <token>`.

> Это демонстрационный клиент лицензирования/heartbeat. Доставка релизов Vanga пока намеренно не подключена: текущий release pipeline jsint-site ещё содержит Workspace Organizer-специфичные поля и будет обобщён отдельно.

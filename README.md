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

## Серверный демонстрационный сервис

Vanga можно держать отдельным локальным HTTP-сервисом и использовать из сайта через reverse/backend proxy. Наружу порт модели открывать не требуется.

### Первый запуск на Ubuntu

```bash
sudo mkdir -p /opt/vanga
sudo chown -R $USER:$USER /opt/vanga
git clone https://github.com/cyrox007/vanga.git /opt/vanga
cd /opt/vanga
git checkout feature/demo-service

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Первый импорт IMDb и обучение.
.venv/bin/python ds_update.py
.venv/bin/python traning.py
```

Проверка API вручную:

```bash
.venv/bin/gunicorn --workers 1 --threads 2 --bind 127.0.0.1:9100 api:app

curl http://127.0.0.1:9100/health
```

### systemd

В репозитории лежат готовые units:

- `deploy/systemd/vanga.service` — постоянно работающий inference API;
- `deploy/systemd/vanga-retrain.service` — одно обучение;
- `deploy/systemd/vanga-retrain.timer` — еженедельный запуск обучения.

Units рассчитаны на пользователя `vanga` и каталог `/opt/vanga`.

```bash
sudo useradd --system --home /opt/vanga --shell /usr/sbin/nologin vanga || true
sudo chown -R vanga:vanga /opt/vanga

sudo cp deploy/systemd/vanga.service /etc/systemd/system/
sudo cp deploy/systemd/vanga-retrain.service /etc/systemd/system/
sudo cp deploy/systemd/vanga-retrain.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now vanga.service
sudo systemctl enable --now vanga-retrain.timer

systemctl status vanga.service
systemctl list-timers vanga-retrain.timer
```

### Как переживается неудачное обучение

Новая модель сохраняется в отдельный каталог `models/releases/<generation>`. Только после успешного сохранения и контрольной загрузки атомарно обновляется `models/current.json`.

Если обучение завершилось с ошибкой, указатель не меняется и API продолжает работать на предыдущем поколении. При неудачной горячей загрузке нового поколения уже запущенный сервис также оставляет предыдущую модель активной.

По умолчанию хранятся три последних успешных поколения модели.

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

### Признаки сценариста

Новая model schema добавляет два pre-release признака первого указанного
сценариста:

- `writer_id` — категориальный IMDb ID;
- `writer_avg_rating` — средний рейтинг его прошлых фильмов строго до года
  прогнозируемого фильма.

Источник — официальный `title.crew.tsv.gz`. При обновлении datasets строится
компактная таблица `title_writers`, а `name_basics` дополняется сценаристами.
Как и для режиссёров/актёров, будущие работы не участвуют в history-статистике.

Старое поколение модели продолжает работать: writer input игнорируется, пока
в metadata активной модели нет `writer_id` и `writer_avg_rating`. Чтобы новый
признак реально влиял на прогноз, после обновления IMDb БД нужен новый retrain.

Порядок первого обновления после появления writer schema:

```bash
.venv/bin/python ds_update.py
.venv/bin/python traning.py --smoke
.venv/bin/python traning.py
```

`ds_update.py` проверяет не только свежесть файлов, но и наличие новой схемы.
Если datasets не изменились, а `title_crew/title_writers` ещё нет, БД всё
равно будет атомарно пересобрана.

### Русский поиск с вариантами и опечатками

Autocomplete использует два слоя:

1. точное сопоставление русского label/alias с IMDb ID;
2. если точного совпадения нет, короткий `wbsearchentities` запрос Wikidata
   предлагает несколько близких вариантов.

Например, строка `Кристофер Ноллан` может привести к кандидату
`Christopher Nolan`, а `Интерстелар` — к `Interstellar`. Wikidata при
этом не подменяет локальную IMDb: найденный P345 обязательно проверяется по
`imdb.duckdb`, а для персон дополнительно проверяется роль
`director`/`writer`/`actor`.

Fuzzy-поиск используется только в autocomplete. Если Wikimedia недоступна,
локальный английский поиск и inference продолжают работать. Результаты fuzzy
lookup ограниченно кешируются в памяти процесса.
### Discovery API для публичного демо

Для формы на JSInteractive сервис предоставляет локальный поиск по уже
подготовленной IMDb-базе:

```bash
curl 'http://127.0.0.1:9100/search/movies?q=Interstellar&year=2014'
curl 'http://127.0.0.1:9100/search/people?q=Nolan&role=director'
```

Поиск фильма возвращает данные, которыми можно автоматически заполнить форму:
IMDb ID, каноническое название, год, длительность, жанры, режиссёра,
сценариста и до пяти актёров. Для кириллического ввода поверх локальной IMDb используется
ограниченный alias-resolution через Wikidata; при недоступности Wikimedia
локальный сервис не падает.

`POST /predict` дополнительно возвращает `generation` активной модели и
`imdb_id`, если фильм был выбран из каталога или распознан через alias. Это
позволяет сохранять воспроизводимые снимки прогноза и позднее сравнивать их с
фактическим рейтингом.

Catalog search использует отдельное read-only DuckDB-соединение и отдельную
блокировку, поэтому медленный поиск/alias lookup не должен удерживать основной
inference lock.

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

### Quality gate перед публикацией

Новая candidate-модель не переключает `current.json` автоматически только
потому, что обучение завершилось. Перед публикацией её `test_mae` сравнивается
с metadata активной модели, если обе модели проверены на одном temporal
holdout (`test_year_from/test_year_to`).

По умолчанию допускается ухудшение не более `0.03` пункта MAE:

```bash
export VANGA_TRAIN_MAX_MAE_REGRESSION=0.03
```

Если регрессия больше порога, retrain завершается ошибкой, candidate release
не публикуется, а `models/current.json` остаётся на предыдущем поколении.
Активная CatBoost-модель повторно не загружается: gate читает только её
`metadata.pkl`, поэтому не возвращает старую проблему с удвоением RAM.

Если temporal holdout изменился (например, наступил новый календарный год),
метрики не считаются напрямую сопоставимыми и автоматический gate не блокирует
публикацию только на основании старого MAE. Результат решения сохраняется в
`metadata.pkl` нового поколения в поле `quality_gate`.

### Как переживается неудачное обучение

Новая модель сохраняется в отдельный каталог `models/releases/<generation>`. Перед публикацией проверяются сам файл модели, его размер и метаданные; затем атомарно обновляется `models/current.json`. Повторная загрузка второй копии CatBoost-модели в процессе retrain намеренно не выполняется, чтобы не удваивать потребление RAM на малом сервере.

Если обучение завершилось с ошибкой, указатель не меняется и API продолжает работать на предыдущем поколении. При неудачной горячей загрузке нового поколения уже запущенный сервис также оставляет предыдущую модель активной.

По умолчанию хранятся три последних успешных поколения модели.


## Обогащение Wikidata и Wikipedia

Дополнительные признаки собираются отдельно от основной `imdb.duckdb`, чтобы сетевые ошибки Wikimedia не мешали inference и обучению. Результат сохраняется в `enrichment.duckdb`.

Сборщик использует:

- Wikidata по IMDb ID (`P345`) для сопоставления фильма с QID;
- структурные свойства Wikidata: режиссёры, актёры, сценаристы, студии, страны, языки, жанры, серия/франшиза, первоисточник, даты релиза;
- английскую и русскую Wikipedia для секций `Plot/Synopsis/Premise` и `Сюжет/Содержание`;
- revision ID Wikipedia, чтобы происхождение текста можно было воспроизвести.

Wikimedia требует информативный `User-Agent`. По умолчанию используется URL репозитория, но для серверной установки лучше задать собственный контакт:

```bash
export VANGA_WIKIMEDIA_USER_AGENT='KinoVanga/0.1 (https://jsinteractive.ru; admin@example.org)'
```

Постепенное наполнение свежих фильмов:

```bash
.venv/bin/python enrich.py \
  --since-year 2024 \
  --max-items 100 \
  --batch-size 10
```

Продолжение следующего запуска идёт с сохранённого курсора. Для полного повторного прохода:

```bash
.venv/bin/python enrich.py --since-year 2024 --reset-cursor
```

Повтор временных ошибок:

```bash
.venv/bin/python enrich.py --retry-errors --max-items 100
```

Для постоянного медленного наполнения есть `vanga-enrich.service` и `vanga-enrich.timer`. Таймер обрабатывает ограниченное число фильмов за запуск и не создаёт высокую нагрузку на Wikimedia.

Важно: enrichment не заменяет обновление исходных IMDb datasets. Если локальная `imdb.duckdb` старая, сначала нужно обновить сам IMDb source pipeline; Wikimedia обогатит только фильмы, уже известные локальной базе.


### Безопасное обучение на малом сервере

Полное обучение больше не собирает все батчи в один pandas DataFrame. Подготовленные признаки пишутся в `temp/training/<generation>/train.tsv` и `test.tsv`, после чего CatBoost читает файловые Pool.

Защита retrain-сервиса:

- один поток CatBoost;
- `used_ram_limit=900mb`;
- systemd `MemoryHigh=1G`;
- systemd `MemoryMax=1200M`;
- `MemorySwapMax=2G`;
- `CPUQuota=50%`;
- повышенный `OOMScoreAdjust`, чтобы при нехватке памяти первым завершался retrain, а не inference;
- минимум 3 ГБ свободного диска сохраняется как резерв. Порог меняется через `VANGA_TRAIN_MIN_FREE_DISK_GB`;
- опубликованная модель по умолчанию ограничена 512 МБ (`VANGA_TRAIN_MAX_MODEL_SIZE_MB`), чтобы oversized-модель не попала в inference.

Если retrain превышает лимиты или заканчивается ошибкой, новая модель не публикуется. `models/current.json` остаётся на предыдущем успешном поколении, поэтому работающий Vanga API продолжает обслуживать запросы старой моделью.

Перед первым полным retrain после изменения параметров CatBoost рекомендуется smoke-проверка:

```bash
.venv/bin/python traning.py --smoke
```

Smoke-режим проходит весь датасет, но обучает только 50 итераций, сериализует временную модель, выводит её размер и удаляет артефакт. `models/current.json` при этом не создаётся и не меняется. Для более короткой отладки можно дополнительно передать `--max-batches N`, но такая проверка уже не отражает полную кардинальность категориальных признаков.

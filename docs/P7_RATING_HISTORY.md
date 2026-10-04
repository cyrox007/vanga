# P7. IMDb Rating History

Официальный IMDb non-commercial dataset хранит текущее состояние `averageRating/numVotes`, но не историческую кривую. Поэтому Vanga начинает накапливать собственную point-in-time историю.

## Принципы

- история физически отделена в `rating_history.duckdb`;
- прошлые snapshots не перезаписываются;
- один официальный snapshot на UTC-день;
- при повторном вызове с тем же source fingerprint операция идемпотентна;
- попытка заменить snapshot того же дня другим source fingerprint блокируется;
- хранится точный `observed_at`;
- сохраняются `averageRating` и `numVotes`;
- отсутствие rating у отслеживаемого фильма отражается через `missing_count`, а не фиктивный рейтинг;
- сбор не используется как pre-release feature автоматически.

## Watchlist

Ежедневно копировать весь IMDb `title_ratings` слишком дорого и бессмысленно. P7 хранит только активный watchlist фильмов, которые Vanga действительно отслеживает.

Добавить фильмы:

```bash
python scripts/rating_history.py watch tt1234567,tt7654321 --source prediction
```

Отключить дальнейший сбор без удаления старой истории:

```bash
python scripts/rating_history.py unwatch tt1234567
```

## Daily capture

`ds_update.py` после успешной проверки/публикации локальной IMDb DuckDB вызывает P7 capture автоматически.

Если watchlist пуст, snapshot не создаётся.

Ошибка P7 capture не откатывает уже успешно обновлённую IMDb БД: она логируется отдельно. Это накопительный вспомогательный слой, а не условие валидности current IMDb snapshot.

Ручной запуск:

```bash
python scripts/rating_history.py capture \
  --imdb-db ./imdb.duckdb \
  --source-fingerprint <sha256>
```

## Point-in-time queries

Временной ряд:

```bash
python scripts/rating_history.py history tt1234567
```

Последнее наблюдение не позже cutoff:

```bash
python scripts/rating_history.py as-of tt1234567 2026-10-04T12:00:00Z
```

Это принципиально отличается от текущего IMDb rating: ответ содержит `point_in_time=true` и привязан к реальному времени нашего наблюдения.

## Что пока нельзя получить

Запуск P7 сегодня не восстанавливает настоящую кривую рейтинга фильмов за 2025–2026 задним числом. Для старых релизов текущий rating может быть итоговым target, но не должен называться рейтингом на дату премьеры.

Цели `rating_early`, `rating_30d`, `rating_180d`, `rating_long_term` появятся только после накопления достаточной временной истории и достоверных release dates.

## Переменная окружения

```bash
VANGA_RATING_HISTORY_DB=/path/to/rating_history.duckdb
```

# P9. TMDb как второй источник дат релиза

`TmdbFutureReleaseCollector` добавляет в P9 второй независимый источник региональных дат выхода фильма. Он не заменяет Wikidata и не выбирает «правильную» дату между источниками эвристически.

## Назначение

Wikidata остаётся discovery/source-of-record слоем с statement-level provenance. TMDb используется как дополнительное подтверждение региональных дат релиза для проектов, у которых уже известен IMDb ID.

Inference не обращается к TMDb. Collector выполняется отдельно, кеширует сырой JSON и импортирует нормализованные release windows в локальную DuckDB.

## Identity resolution

Проект должен уже существовать в P9 registry и иметь валидный `imdb_id`.

Collector вызывает TMDb `/find/{imdb_id}?external_source=imdb_id` и принимает результат только когда найден ровно один movie result. Если результат отсутствует или неоднозначен, проект не разрешается автоматически и возвращается warning `tmdb_movie_not_resolved`.

## Какие даты импортируются

Из `movie/{tmdb_id}/release_dates` используются только theatrical release types:

- `2` — Theatrical (limited);
- `3` — Theatrical.

Digital, Physical и TV release не используются как дата кинотеатрального релиза.

Территория сохраняется явно в виде:

```text
iso3166:US
iso3166:GB
iso3166:DE
```

Дата хранится как `precision=exact`. Collector не создаёт искусственный `worldwide` release.

## Provenance

Каждый refresh создаёт immutable source snapshot:

```text
tmdb:<tmdb_id>:release-dates:<timestamp>
```

Raw response кешируется в `data/future_releases/tmdb/`. Fingerprint raw payload и нормализованного bundle входит в fingerprint batch.

## Настройка

TMDb API требует application authentication. Collector использует Read Access Token в Bearer header:

```bash
export TMDB_READ_ACCESS_TOKEN='...'
```

На сервере переменную следует передать только collector/refresh процессу. Она не нужна Vanga inference API.

## Запуск

Один проект:

```bash
python scripts/tmdb_future_releases.py \
  --project-id wikidata:Q123 \
  --import
```

Несколько проектов:

```bash
python scripts/tmdb_future_releases.py \
  --project-id wikidata:Q123 \
  --project-id wikidata:Q456 \
  --import
```

Без `--project-id` collector берёт проекты из P9 registry в пределах `--limit`.

## Что остаётся следующим шагом

Wikidata сейчас сохраняет территорию qualifier-а как `wikidata:<QID>`, а TMDb — как `iso3166:<CC>`. Для полноценного автоматического cross-source conflict detection нужен отдельный canonical territory resolver, который свяжет Wikidata country/territory QID с ISO 3166-1 code без потери исходного provenance.

До появления этого resolver источники остаются независимыми и не должны искусственно сливаться по названию страны.

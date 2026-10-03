# Source Context — pre-release первоисточник

## Назначение

Source Context хранит только факты о первоисточнике и формате адаптации, которые могут быть известны **до релиза** проекта.

- `src/source_context.py` — canonical source/creator/project registry и base as-of features;
- `src/source_team_history.py` — опыт текущей creative team именно на прошлых адаптациях;
- `src/source_format_pressure.py` — прозрачные source-age/series/runtime proxies;
- `scripts/source_context.py` — CLI;
- отдельная `source_context.duckdb` (`VANGA_SOURCE_CONTEXT_DB`).

Слой физически отделён от retrospective `adaptation.duckdb`: пострелизные plot summaries, StoryMap/StoryDiff, критика и фактические изменения готовой адаптации сюда не попадают.

## Temporal/provenance контракт

Project↔source link видим только при `known_at <= cutoff`. Creator-link также имеет собственный `known_at`.

Книга/игра может существовать десятилетиями, но если связь будущего фильма с ней была объявлена позже, ранний прогноз её не использует.

Для adaptation-team history prior project учитывается только если:

1. source-link prior project известен к cutoff;
2. prior project уже выпущен к cutoff;
3. prior project выпущен раньше target release limit;
4. target project исключён;
5. future project исключён даже при заранее известной adaptation identity.

## Canonical source works

`source_works` хранит work ID, title, source type, first publication date, optional series ID/position/size и external ID.

Source types: novel/novel series/short story/comic/graphic novel/manga/game/play/musical/TV/film/remake/real events/biography/mythology/other.

## Creators

`source_creators` + `source_work_creators` хранят canonical creator identity, роль, provenance и `known_at`.

Raw creator ID пока не является ML feature; base snapshot возвращает neutral creator count.

## Project format

`source_context_projects` хранит adaptation format, planned runtime, episode count/runtime, release date и `format_known_at`.

Если формат стал известен позже cutoff, ранний snapshot получает `source_format_known=0` и явные numeric placeholders.

## Project ↔ source relations

Поддерживаются `based_on`, `adaptation_of`, `remake_of`, `inspired_by`, `based_on_real_events`, `other`.

Есть primary flag, но проект может ссылаться на несколько source works. Повторное provenance-подтверждение того же work/relation не удваивает агрегаты.

## Base feature contract

`SourceContextStore.features_as_of` возвращает:

- source-known/work/primary-work counts;
- creator count;
- source age coverage + mean/min/max относительно cutoff;
- series-size coverage + mean/max;
- series-position mean;
- planned runtime/episodes/total runtime;
- source-type counts;
- relation-type counts;
- adaptation-format one-hot.

Неизвестная publication date снижает coverage, но не становится фиктивным нулём внутри mean.

## Source Format Pressure

`SourceFormatPressureContext.features_as_of` добавляет прозрачные pre-release proxies, рассчитанные только из уже известных source/series/project-format facts.

Это **не настоящий `adaptation_compression_ratio`**. Без структурированного понимания сюжетных арок нельзя честно утверждать, что фильм «сжал 70% материала».

### Возраст первоисточника к target release

Если release/format metadata уже известны на cutoff, считаются:

- `source_age_at_release_known_ratio`;
- `source_age_at_release_years_mean/min/max`;
- `source_publication_after_release_count`.

Source с publication date после target release не получает отрицательный возраст. Он исключается из age aggregate и увеличивает отдельный data-quality counter.

### Series context

По видимым source works считаются:

- `source_series_count`;
- `source_series_progress_known_ratio`;
- `source_series_progress_ratio_mean/max`.

`series_progress = series_position / series_size` используется только при известных position и size. Это положение source work внутри серии, а не оценка сюжетной сложности.

### Runtime / episode pressure proxies

При заранее известном planned runtime считаются:

- `source_format_pressure_runtime_known`;
- `source_runtime_per_linked_work_known`;
- `source_runtime_per_linked_work_minutes`;
- `source_runtime_per_primary_work_known`;
- `source_runtime_per_primary_work_minutes`;
- `source_episodes_per_linked_work_known`;
- `source_episodes_per_linked_work`.

Дополнительно возвращаются `source_type_diversity` и `source_relation_diversity`.

Эти признаки означают только «сколько запланированного экранного времени приходится на известное число связанных works». Они не утверждают, что каждая книга/игра/комикс имеет одинаковый объём содержания.

## Adaptation-specific team history

`SourceTeamHistory.features_as_of` использует Source Context + локальную IMDb БД и отвечает на отдельный вопрос: **какой опыт текущая creative team уже имела именно на более ранних адаптациях**.

### Режиссёрская команда

Несколько режиссёров учитываются как нормальный случай. Для каждого current director отдельно считаются:

- число prior adaptations;
- число prior adaptations с совпадающим source type;
- число prior adaptations с genre overlap target-фильма.

Затем возвращаются `known_ratio`, count mean и count max по всей режиссёрской команде. Explicit `Unknown` остаётся в denominator как ноль.

### Сценарист

Для current writer:

- `source_writer_adaptation_count`;
- `source_writer_adaptation_known`;
- same-source-type adaptation count;
- same-genre adaptation count.

### Director↔writer pair

Для каждой пары current director × current writer отдельно считается previous adaptation collaboration, затем агрегируется по director team:

- pair known ratio;
- count mean/max;
- same-source-type pair mean/max;
- same-genre pair mean/max.

Это не общий cohesion score и не рейтинг качества.

### Почему пока только counts

P3 team history намеренно не использует IMDb rating прошлых адаптаций. Сначала проверяется factual experience/coverage signal без дополнительной temporal неоднозначности outcome rating.

## CLI

```bash
python scripts/source_context.py init
python scripts/source_context.py import data/source-context/example.json
python scripts/source_context.py snapshot PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py links PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py format-pressure PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py team-history PROJECT_ID 2026-01-15T00:00:00Z
```

Обычный `snapshot` объединяет base Source Context и Source Format Pressure. `format-pressure` выводит тот же derived блок отдельно и явно возвращает `compression_ratio: null`, чтобы proxy не путали с измеренной степенью сжатия.

При необходимости resolved IDs можно передать явно:

```bash
python scripts/source_context.py team-history PROJECT_ID 2026-01-15T00:00:00Z \
  --directors nm0000001,nm0000002 --writer nm0000003 --imdb-db /path/to/imdb.duckdb
```

Без override режиссёры и writer разрешаются по IMDb target project.

JSON bundle order:

`sources → works → creators → creator_links → projects → source_links`.

## Что сознательно отсутствует

Pre-release Source Context не содержит full source text, post-release plot, StoryMap/StoryDiff, expert opinion или post-release adaptation facts.

Source popularity и содержательная complexity/compression появятся только при воспроизводимом point-in-time источнике/методе. Текущий Format Pressure — лишь transparent factual proxy layer.

## Путь к ML

Перед включением source/team-history/format-pressure features в CatBoost требуется реальное historical наполнение, coverage analysis, train/inference parity, отдельный temporal ablation, quality gate и проверка VPS.

Следующий P3-инкремент: research foundation для source complexity/compression, где каждый complexity fact будет явно иметь provenance/method/version и не будет опираться на post-release фильм.

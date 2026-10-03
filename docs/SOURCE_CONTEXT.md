# Source Context — pre-release первоисточник

## Назначение

Source Context хранит только факты о первоисточнике и формате адаптации, которые могут быть известны **до релиза** проекта.

Основные слои:

- `src/source_context.py` — canonical source/creator/project registry и base as-of features;
- `src/source_team_history.py` — опыт current creative team именно на прошлых адаптациях;
- `src/source_format_pressure.py` — прозрачные source-age/series/runtime proxies;
- `src/source_complexity.py` — versioned research measurements сложности первоисточника;
- `scripts/source_context.py` — CLI;
- отдельная `source_context.duckdb` (`VANGA_SOURCE_CONTEXT_DB`).

Слой физически отделён от retrospective `adaptation.duckdb`: пострелизные plot summaries, StoryMap/StoryDiff, критика и фактические изменения готовой адаптации сюда не попадают.

## Temporal/provenance контракт

Project↔source link видим только при `known_at <= cutoff`. Creator-link и complexity measurement также имеют собственный `known_at`.

Книга, игра или комикс могут существовать десятилетиями, но если связь будущего фильма с ними была объявлена позже, ранний прогноз эту связь не использует.

Для adaptation-team history prior project учитывается только если:

1. source-link prior project известен к cutoff;
2. prior project уже выпущен к cutoff;
3. prior project выпущен раньше target release limit;
4. target project исключён;
5. future project исключён даже при заранее известной adaptation identity.

## Canonical source works

`source_works` хранит work ID, title, source type, first publication date, optional series ID/position/size и external ID.

Source types включают novel/novel series/short story/comic/graphic novel/manga/game/play/musical/TV/film/remake/real events/biography/mythology/other.

## Creators

`source_creators` + `source_work_creators` хранят canonical creator identity, роль, provenance и `known_at`.

Raw creator ID пока не является ML feature; base snapshot возвращает neutral creator count.

## Project format

`source_context_projects` хранит adaptation format, planned runtime, episode count/runtime, release date и `format_known_at`.

Если формат стал известен позже cutoff, ранний snapshot получает `source_format_known=0` и numeric placeholders.

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

`SourceFormatPressureContext.features_as_of` добавляет прозрачные pre-release proxies из уже известных source/series/project-format facts.

Это **не настоящий `adaptation_compression_ratio`**. Без структурированного понимания сюжетных арок нельзя честно утверждать, что фильм «сжал N% материала».

Слой считает:

- source age at target release и coverage;
- data-quality counter publication-after-release;
- series count/progress;
- planned runtime/episodes на linked/primary source work;
- source-type diversity;
- relation diversity.

Эти признаки означают только доступный формат/объём планируемого экранного времени на известное число works.

## Adaptation-specific team history

`SourceTeamHistory.features_as_of` использует Source Context + локальную IMDb БД и отвечает на вопрос: **какой опыт current creative team уже имела именно на более ранних адаптациях**.

### Режиссёрская команда

Несколько режиссёров учитываются как нормальный случай. Для каждого current director отдельно считаются:

- число prior adaptations;
- prior adaptations с совпадающим source type;
- prior adaptations с genre overlap target-фильма.

Затем возвращаются `known_ratio`, count mean и count max по всей director team. Explicit `Unknown` остаётся в denominator как ноль.

### Сценарист и director↔writer pair

Для writer считаются adaptation count, same-source-type count и same-genre count.

Для каждой current director × writer пары отдельно считается previous adaptation collaboration, затем агрегируется по director team. Это не общий cohesion score и не рейтинг качества.

P3 team history пока использует counts/coverage, а не IMDb rating прошлых адаптаций.

## Source Complexity research foundation

`SourceComplexityContext` хранит measurements первоисточника как **versioned snapshots**.

Каждый snapshot содержит точные `method + method_version`, `measured_at`, `known_at`, provenance source, coverage и raw metrics. Разные методики не смешиваются автоматически.

Разрешены воспроизводимые raw counts/lengths: words/pages/minutes, chapters, characters, major characters, plotlines, major arcs, locations, factions, worldbuilding entities/relations.

Произвольный «complexity score» schema не принимает.

Density вычисляется только внутри одного snapshot, например characters/10k words. Нельзя объединять count из method A с length из method B.

Для проекта с несколькими source works непокрытые works остаются в denominator и снижают coverage, но не получают fallback.

Подробный контракт: `docs/SOURCE_COMPLEXITY.md`.

## CLI

```bash
python scripts/source_context.py init
python scripts/source_context.py import data/source-context/example.json
python scripts/source_context.py snapshot PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py links PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py format-pressure PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py team-history PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py complexity-methods PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py complexity-features PROJECT_ID 2026-01-15T00:00:00Z \
  --method manual-structural-counts --version 1
python scripts/source_context.py complexity-snapshots WORK_ID 2026-01-15T00:00:00Z
```

Обычный `snapshot` объединяет base Source Context и Source Format Pressure. Complexity остаётся отдельным research-only вызовом и требует явной method/version.

При необходимости resolved IDs creative team можно передать явно:

```bash
python scripts/source_context.py team-history PROJECT_ID 2026-01-15T00:00:00Z \
  --directors nm0000001,nm0000002 --writer nm0000003 --imdb-db /path/to/imdb.duckdb
```

JSON bundle order:

`sources → works → creators → creator_links → projects → source_links → complexity_snapshots`.

## Что сознательно отсутствует

Pre-release Source Context не содержит full film plot, post-release StoryMap/StoryDiff, expert opinion или фактические post-release adaptation changes.

Source Complexity foundation описывает только воспроизводимые measurements первоисточника. Содержательные causal/structural признаки относятся к P4.

## Путь к ML

Перед включением source/team-history/format-pressure/complexity features в CatBoost требуется реальное historical наполнение, coverage analysis, train/inference parity, отдельный temporal ablation, quality gate и VPS profiling.

P3 foundation после Source Complexity считается собранным. Следующий крупный этап — **P4 automatic `text -> StoryMap -> StoryDiff`**.

Перед финальным retrain дополнительно обязателен отдельный **Data Freshness/Backfill guard** для IMDb snapshot: свежесть dump, покрытие 2024–2026, maturity target и воспроизводимый manifest/fingerprint.

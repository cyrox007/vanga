# Source Context — pre-release первоисточник

## Назначение

`src/source_context.py` хранит только факты о первоисточнике и формате адаптации, которые могут быть известны **до релиза** фильма/сериала.

Этот слой физически отделён от `adaptation.duckdb` и retrospective StoryMap/StoryDiff. Пострелизный пересказ сюжета, критика и фактические изменения адаптации сюда не попадают.

База по умолчанию: `source_context.duckdb` (`VANGA_SOURCE_CONTEXT_DB`).

## Temporal/provenance контракт

Связь проекта с source work видима только при `project_source_links.known_at <= cutoff`.

Creator-link также имеет собственный `known_at`.

Это важно: книга/игра/комикс может существовать десятилетиями, но если конкретная связь будущего фильма с материалом была объявлена позже, более ранний прогноз не имеет права её использовать.

## Canonical source works

`source_works` хранит:

- `work_id`;
- title;
- `source_type`;
- first publication date;
- optional `series_id`;
- series position/size;
- external ID.

Поддерживаемые source types включают novel/series/short story/comic/graphic novel/manga/game/play/musical/TV/film/remake/real events/biography/mythology/other.

## Creators

`source_creators` + `source_work_creators` хранят canonical creator identity и роль с provenance/`known_at`.

Raw creator ID пока не является ML feature: foundation возвращает только neutral creator coverage/count. История режиссёра/сценариста именно на адаптациях будет отдельным P3-инкрементом.

## Project format

`source_context_projects` хранит заранее известные параметры экранизации:

- adaptation format: film/miniseries/series/animation;
- planned runtime;
- planned episode count;
- planned episode runtime;
- release date;
- `format_known_at`.

Если формат/план стали известны позже cutoff, ранний snapshot получает `source_format_known=0` и нулевые numeric placeholders.

## Project ↔ source relations

`project_source_links` поддерживает:

- `based_on`;
- `adaptation_of`;
- `remake_of`;
- `inspired_by`;
- `based_on_real_events`;
- `other`.

Есть `is_primary`, но система поддерживает несколько source works. Повторное подтверждение того же work/relation не увеличивает агрегаты.

## Feature contract

`features_as_of(project_id, cutoff)` возвращает:

- `source_context_known`;
- source work count / primary work count;
- creator count;
- source age coverage + mean/min/max;
- series-size coverage + mean/max;
- series-position mean;
- planned runtime/episodes/total runtime;
- source-type counts;
- relation-type counts;
- one-hot adaptation format.

Неизвестная publication date снижает `source_age_known_ratio`, но не превращается в фиктивный возраст 0 внутри среднего.

## Что пока сознательно отсутствует

Foundation **не** содержит:

- полный текст source;
- plot summary;
- StoryMap/StoryDiff;
- post-release adaptation changes;
- экспертные оценки;
- source popularity без воспроизводимого point-in-time источника;
- автоматически выдуманную worldbuilding/character complexity.

Эти блоки добавляются отдельно и проходят собственную temporal/quality проверку.

## CLI

```bash
python scripts/source_context.py init
python scripts/source_context.py import data/source-context/example.json
python scripts/source_context.py snapshot PROJECT_ID 2026-01-15T00:00:00Z
python scripts/source_context.py links PROJECT_ID 2026-01-15T00:00:00Z
```

Порядок JSON bundle:

`sources → works → creators → creator_links → projects → source_links`.

## Путь к ML

Перед включением source-context features в основную модель требуется:

1. реальное воспроизводимое наполнение;
2. достаточный coverage на historical movies;
3. train/inference parity;
4. отдельный temporal ablation;
5. quality gate;
6. проверка ресурсоёмкости VPS.

Следующие P3-инкременты: source age at adaptation/release, adaptation-team history по типу source/жанру и исследовательские complexity/compression proxies, не использующие post-release leakage.

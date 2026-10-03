# Source Complexity — research foundation

## Назначение

`src/source_complexity.py` хранит воспроизводимые измерения сложности первоисточника для P3. Это **research-only** слой: он не включён в CatBoost и не является «оценкой качества» или готовым `adaptation_compression_ratio`.

Главный принцип: complexity хранится как **versioned measurement snapshot**. Любая выборка требует точного `method + method_version`; результаты разных методик автоматически не смешиваются и не усредняются.

## Temporal/provenance контракт

Каждый snapshot содержит:

- `work_id`;
- `method`;
- `method_version`;
- `measured_at`;
- `known_at`;
- `source_id` provenance;
- `coverage_fraction`;
- raw `metrics`;
- optional note.

`known_at` не может быть раньше `measured_at`. Pre-release snapshot использует только measurements с `known_at <= cutoff`.

Если один work измерялся одной методикой несколько раз, для feature snapshot берётся последнее известное измерение этой exact method/version. Измерения другой версии не подменяют его.

## Разрешённые raw metrics

Foundation принимает только наблюдаемые/воспроизводимые counts и lengths:

- `source_length_words`;
- `source_length_pages`;
- `source_length_minutes`;
- `chapter_count`;
- `character_count`;
- `major_character_count`;
- `plotline_count`;
- `major_arc_count`;
- `location_count`;
- `faction_count`;
- `worldbuilding_entity_count`;
- `worldbuilding_relation_count`.

Произвольные поля вроде `complexity_score`, `quality_score` или `adaptation_difficulty` отвергаются schema validation.

## Derived density

Density вычисляется только внутри одного measurement snapshot и только если в нём есть `source_length_words`.

Поддерживаются:

- character density / 10k words;
- major-character density / 10k words;
- plotline density / 10k words;
- major-arc density / 10k words;
- worldbuilding entity density / 10k words;
- worldbuilding relation density / 10k words.

Нельзя взять `character_count` из method A и `source_length_words` из method B.

## Coverage

Для проекта с несколькими source works возвращаются:

- linked work count;
- measured work count;
- work coverage ratio;
- mean measurement coverage;
- `known_ratio`, `mean`, `max` для каждого raw/derived metric.

Непокрытый work остаётся в denominator. Он не получает fallback и не превращается в fictive «среднюю сложность».

## CLI

Показать доступные protocol/version и их coverage:

```bash
python scripts/source_context.py complexity-methods PROJECT_ID 2026-01-15T00:00:00Z
```

Получить features строго для одной версии метода:

```bash
python scripts/source_context.py complexity-features PROJECT_ID 2026-01-15T00:00:00Z \
  --method manual-structural-counts --version 1
```

Посмотреть measurements конкретного source work:

```bash
python scripts/source_context.py complexity-snapshots WORK_ID 2026-01-15T00:00:00Z \
  --method manual-structural-counts --version 1
```

`complexity_snapshots` также можно импортировать обычным JSON bundle Source Context после `sources` и `works`.

## Что здесь сознательно отсутствует

Этот слой не содержит film StoryMap, StoryDiff, сохранность мотиваций, causal links адаптации, экспертные оценки или post-release критику. Они относятся к P4/P5 и не должны утекать в pre-release source facts.

## Путь к ML

До любого включения complexity features в production model обязательны:

1. фиксированная method/version;
2. достаточное historical coverage;
3. одинаковый extractor для train и inference;
4. temporal ablation;
5. quality gate;
6. VPS profiling.

Следующий крупный этап после этого foundation — P4 automatic `text -> StoryMap -> StoryDiff`. Перед ним отдельно вводится Data Freshness/Backfill guard для IMDb snapshot, чтобы финальный retrain не работал на устаревшей базе.

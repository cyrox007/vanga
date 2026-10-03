# Production Context — контракт признаков производства

## Статус реализации

Фундамент registry реализован отдельно от IMDb и CatBoost:

- `src/production_context.py` — schema/validation/temporal snapshots;
- `scripts/production_context.py` — init/import/snapshot/timeline CLI;
- отдельная `production_context.duckdb` через `VANGA_PRODUCTION_CONTEXT_DB`;
- каждый факт требует provenance и `known_at`;
- ML-интеграции пока **нет**: registry сначала наполняется и проверяется, а candidate-признаки допускаются в модель только отдельным temporal ablation.

## Зачем нужен отдельный слой

Качество фильма определяется не только режиссёром, сценаристом и актёрами. У больших студийных проектов значительная часть решений принимается на уровне продюсеров, франшизы, shared universe, студии, тестовых показов, переписываний и внешних консультантов.

Поэтому Vanga не должна сводить фильм к одному `director_id` и не должна делать причинные выводы вида «студия/консультант/движение X ухудшает фильм». Производственные факторы сначала фиксируются как наблюдаемые события и только потом проверяются на temporal holdout.

## Temporal/provenance контракт

Для каждого производственного факта принципиально различаются:

- `event_at` — когда событие физически произошло, если дата известна;
- `known_at` — когда информация стала доступна наблюдателю/публичному источнику;
- `source_id` — источник с URL, publisher, датой публикации/получения и confidence.

Pre-release snapshot использует **только `known_at <= cutoff`**. Например, смена режиссёра могла произойти в январе, но если о ней достоверно сообщили только после релиза, январский прогноз не имеет права использовать этот факт.

`features_as_of(project_id, cutoff)` и `timeline_as_of(project_id, cutoff)` реализуют именно этот контракт.

## Registry

### Проект

`production_projects` хранит:

- внутренний `project_id` и необязательный IMDb ID;
- название и release date;
- `franchise_id`;
- `shared_universe_id`;
- `installment_index`;
- `identity_known_at` — когда эти identity-факты были известны.

### Сущности и роли

`production_entities` — нейтральный справочник:

- studio;
- production company / production label;
- producer;
- creative lead;
- consultancy;
- other.

`project_entity_links` связывает сущность с фильмом, ролью, стадией производства, `known_at` и provenance.

Название компании или человека само по себе не является положительным/отрицательным признаком.

### Production events

`production_events` поддерживает типы:

- `director_change`;
- `writer_change`;
- `creative_lead_change`;
- `release_date_change`;
- `rewrite`;
- `reshoot`;
- `recut`;
- `format_change`;
- `scope_change`;
- `production_label_change`;
- `other`.

У события есть stage, `event_at`, `known_at`, source и структурированный `details` JSON. Интерпретация причины/качества в event не записывается.

### External creative consultancies

`consultancy_engagements` хранит:

- consultancy entity;
- scope: story / script / character / worldbuilding / authenticity / sensitivity / other;
- stage: development / writing / pre-production / production / post-production / release;
- `known_at`;
- source/provenance.

Таким образом условная внешняя narrative/sensitivity компания фиксируется как обычная consultancy entity и набор scope, а не как заранее отрицательный коэффициент.

## Neutral snapshot features

Registry уже умеет строить нейтральный pre-release snapshot, например:

- `production_franchise_known`;
- `production_shared_universe_known`;
- `production_installment_index`;
- `production_studio_count`;
- `production_producer_count`;
- `production_creative_lead_count`;
- `production_change_count`;
- `production_director_change_count`;
- `production_writer_change_count`;
- `production_rewrite_count`;
- `production_reshoot_count`;
- `production_recut_count`;
- `production_consultancy_count`;
- `production_consultancy_story_count`;
- `production_consultancy_script_count`;
- `production_consultancy_character_count`;
- `production_consultancy_worldbuilding_count`;
- `production_consultancy_authenticity_count`;
- `production_consultancy_sensitivity_count`.

Это **candidate proxy set**, а не доказанные факторы качества. Наличие feature в snapshot не означает, что он будет включён в CatBoost.

## CLI

Инициализация:

```bash
python scripts/production_context.py init
```

Импорт JSON bundle:

```bash
python scripts/production_context.py import data/production-context/example.json
```

Bundle применяется в безопасном порядке: `sources → projects → entities → links → events → consultancies`.

Минимальный пример:

```json
{
  "sources": [
    {
      "source_id": "src-1",
      "url": "https://example.org/article",
      "published_at": "2026-01-10T12:00:00Z",
      "confidence": 0.9
    }
  ],
  "projects": [
    {
      "project_id": "tt1234567",
      "imdb_id": "tt1234567",
      "title": "Example Film",
      "franchise_id": "example-franchise",
      "shared_universe_id": "example-universe",
      "installment_index": 3,
      "identity_known_at": "2025-06-01T00:00:00Z"
    }
  ],
  "entities": [
    {
      "entity_id": "consultancy-example",
      "kind": "consultancy",
      "name": "Example Narrative Consultancy"
    }
  ],
  "events": [
    {
      "event_id": "rewrite-1",
      "project_id": "tt1234567",
      "event_type": "rewrite",
      "event_at": "2025-12-01T00:00:00Z",
      "known_at": "2026-01-10T12:00:00Z",
      "stage": "writing",
      "source_id": "src-1",
      "details": {"summary": "Публично подтверждённое переписывание"}
    }
  ],
  "consultancies": [
    {
      "engagement_id": "engagement-1",
      "project_id": "tt1234567",
      "entity_id": "consultancy-example",
      "scope": "story",
      "stage": "writing",
      "known_at": "2026-01-10T12:00:00Z",
      "source_id": "src-1"
    }
  ]
}
```

Snapshot на конкретную дату:

```bash
python scripts/production_context.py snapshot tt1234567 2026-01-15T00:00:00Z
```

Доказательная timeline:

```bash
python scripts/production_context.py timeline tt1234567 2026-01-15T00:00:00Z
```

## Несколько режиссёров

Фильм может иметь полноценную режиссёрскую команду. Начиная со schema v11 API принимает `directors: [...]`, legacy `director` остаётся backward-compatible, а отдельные team-блоки используют все director-credit target-фильма.

Schema v15 дополнительно считает team-wide director↔writer и director↔actor histories по всей режиссёрской команде и всему principal cast. Production Context не дублирует эти IMDb-history признаки, а описывает надстройку производственного процесса.

## Студийная и франшизная модель производства

Для крупных франшиз и shared-universe проектов индивидуальный режиссёр может иметь меньше фактической автономии, чем в независимом кино. Это не означает автоматического ухудшения качества. Vanga должна проверять отдельные измеримые признаки:

- `production_label` / студия / production company;
- `franchise_id` и номер фильма внутри франшизы;
- `shared_universe_id`;
- producer IDs / creative lead, если источник воспроизводим;
- исторический результат продюсера/студии только по более ранним работам;
- число одновременно связанных проектов франшизы как исследовательский proxy сложности координации;
- зависимость от continuity предыдущих фильмов/сериалов как отдельный исследовательский признак.

Marvel и другие крупные shared-universe студии должны быть обычными значениями этого общего контракта, а не специальным штрафом или исключением.

## «Тенденции», движения и культурные веяния

Vanga не должна кодировать политическую или культурную позицию как признак качества и не должна использовать чувствительные характеристики людей.

Вместо этого допустимо измерять конкретные производственные последствия, если они наблюдаемы и датированы:

- число внешних creative mandates/consultancies, когда оно документировано;
- количество крупных переписываний;
- конфликтующие публичные creative directions;
- смены команды;
- scope churn;
- continuity pressure;
- production delay/change count.

Если эксперт утверждает, что определённое культурное или корпоративное влияние ухудшило произведение, в Expert Analysis Corpus это хранится как `expert_interpretation`. Для модели допускается только проверяемый структурный proxy, а не само мнение эксперта.

## Связь с Expert Analysis Corpus

Красный Циник, BadComedian и будущие экспертные профили могут подсказать гипотезы о production pressure, вмешательстве, переписывании или потере авторского замысла.

Контракт остаётся прежним:

`observation -> evidence -> structural_consequence -> expert_interpretation`

Для pre-release Vanga переносится только proxy, который:

1. был доступен до премьеры;
2. имеет источник и дату;
3. не является оценочным ярлыком;
4. проходит отдельный temporal ablation;
5. не ухудшает quality gate.

## Порядок реализации

1. [x] Multi-director foundation и Creative Team schema v11-v15.
2. [~] Factual Production Context registry: projects/entities/provenance/events/consultancies + as-of snapshots.
3. [ ] Нормализованный producer/studio/franchise history и строго исторические агрегаты.
4. [~] Timestamped production-change registry — schema/store готовы, требуется наполнение воспроизводимыми данными.
5. [~] External creative consultancy registry — schema/store готовы, требуется наполнение воспроизводимыми данными.
6. [R] Исследование production-pressure proxies на temporal holdout.
7. [ ] Только после отдельных ablation — решение, какие признаки остаются в production model.

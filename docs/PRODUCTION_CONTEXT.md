# Production Context — контракт признаков производства

## Статус реализации

Фундамент Production Context реализован отдельно от IMDb и CatBoost:

- `src/production_context.py` — factual registry, validation и temporal snapshots;
- `src/production_identity.py` — canonical identity/aliases и historical `as-of` aggregates;
- `scripts/production_context.py` — init/import/snapshot/history/timeline/resolve CLI;
- отдельная `production_context.duckdb` через `VANGA_PRODUCTION_CONTEXT_DB`;
- каждый факт, alias и project-group link имеет provenance и `known_at`;
- ML-интеграции пока **нет**: сначала собирается воспроизводимый factual dataset, затем каждый candidate proxy проверяется отдельным temporal ablation.

## Зачем нужен отдельный слой

Качество фильма определяется не только режиссёром, сценаристом и актёрами. У больших студийных проектов значительная часть решений принимается на уровне продюсеров, франшизы, shared universe, студии, тестовых показов, переписываний и внешних консультантов.

Поэтому Vanga не должна сводить фильм к одному `director_id` и не должна делать причинные выводы вида «студия/консультант/движение X ухудшает фильм». Производственные факторы сначала фиксируются как наблюдаемые события и только потом проверяются на temporal holdout.

## Temporal/provenance контракт

Для каждого производственного факта принципиально различаются:

- `event_at` — когда событие физически произошло, если дата известна;
- `known_at` — когда информация стала доступна наблюдателю/публичному источнику;
- `source_id` — источник с URL, publisher, датой публикации/получения и confidence.

Pre-release snapshot использует **только `known_at <= cutoff`**. Событие могло произойти в январе, но если о нём достоверно сообщили только после релиза, более ранний прогноз не имеет права его использовать.

Для historical aggregates действует дополнительное правило: проект должен быть **уже выпущен к cutoff**. Будущий фильм той же студии/франшизы не считается историческим опытом, даже если его production identity уже публично известна.

## Registry

### Проект

`production_projects` хранит:

- внутренний `project_id` и необязательный IMDb ID;
- название и release date;
- legacy `franchise_id` / `shared_universe_id` / `installment_index` для обратной совместимости;
- `identity_known_at`;
- время обновления записи.

Canonical franchise/shared-universe identity теперь дополнительно хранится через `production_groups` + `project_group_links`, где есть отдельный `source_id` и `known_at`.

### Production entities

`production_entities` — нейтральный canonical-справочник:

- studio;
- production company;
- production label;
- producer;
- creative lead;
- consultancy;
- other.

`project_entity_links` связывает canonical entity с фильмом, ролью, стадией производства, `known_at` и provenance.

Название компании или человека само по себе не является положительным/отрицательным признаком.

### Canonical aliases

`production_entity_aliases` и `production_group_aliases` решают проблему разных написаний одной и той же сущности.

Контракт намеренно строгий:

- alias добавляется **явно**;
- alias требует `source_id` и `known_at`;
- техническая нормализация делает Unicode NFKC/casefold и убирает пунктуационные различия;
- fuzzy matching не выполняется;
- похожие названия автоматически не склеиваются;
- неоднозначный alias возвращает ошибку и требует уточнить kind/canonical ID.

То есть `Marvel Studios`, `Marvel Studios, LLC` и другой вариант могут указывать на один canonical ID только после явной регистрации alias. Система не должна сама решать, что две похожие строки — одна компания.

### Franchise/shared universe groups

`production_groups` хранит canonical группы двух типов:

- `franchise`;
- `shared_universe`.

`project_group_links` связывает фильм с группой и хранит:

- `known_at`;
- `source_id`;
- необязательный `installment_index`;
- note.

Legacy поля в `production_projects` остаются fallback для старых bundle, но новое наполнение должно использовать canonical group links.

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

Таким образом внешняя narrative/sensitivity consultancy фиксируется как обычная entity и набор scope, а не как заранее отрицательный коэффициент.

## Neutral snapshot features

`features_as_of(project_id, cutoff)` строит factual snapshot из известных на дату фактов:

- `production_franchise_known`;
- `production_shared_universe_known`;
- `production_installment_index`;
- `production_studio_count`;
- `production_producer_count`;
- `production_creative_lead_count`;
- `production_change_count` и event-type counts;
- `production_consultancy_count` и scope counts.

Это **candidate proxy set**, а не доказанные факторы качества.

## Historical identity features

`ProductionIdentityHistory.history_features_as_of(project_id, cutoff)` добавляет нейтральную историю production identity только по ранее выпущенным и уже известным проектам.

### Franchise / shared universe

- `production_franchise_prior_project_count`;
- `production_shared_universe_prior_project_count`.

Это пока чистый объём ранее выпущенного контекста. `shared_universe_prior_project_count` можно исследовать как один из proxy continuity load, но нельзя заранее объявлять его отрицательным или положительным.

### Studio / producer / creative lead history

Для `studio`, `production_company`, `production_label`, `producer`, `creative_lead` считаются:

- `production_<role>_history_known_ratio`;
- `production_<role>_prior_project_count_mean`;
- `production_<role>_prior_project_count_max`.

Нулевая история остаётся нулём и влияет на coverage. Никакого фиктивного среднего рейтинга нет.

### Повторное пересечение production-команды

- `production_entity_history_known_ratio`;
- `production_prior_shared_entity_project_count` — сколько прошлых проектов разделяют хотя бы одну текущую canonical entity;
- `production_key_team_repeat_project_count` — сколько прошлых проектов разделяют минимум две текущие production entities;
- `production_prior_shared_entity_max` — максимальное число текущих production entities, встретившихся вместе на одном прошлом проекте.

Это прозрачные raw-history признаки. Единый `production cohesion score` пока не вводится.

## Почему пока нет studio/producer rating

На этом этапе намеренно не вычисляется «средний рейтинг Marvel/студии/продюсера».

Чтобы такой признак был честным, нужен отдельный temporal outcome contract: какой rating был доступен на конкретную дату или какая целевая long-term rating используется одинаково для всех исторических проектов. Текущая IMDb выгрузка содержит актуальное состояние рейтинга, а не историческую кривую.

Поэтому сначала используются только counts/coverage/repeat-history. Rating-based production reputation допускается после появления P7 rating history или отдельного доказанного temporal-протокола.

## CLI

Инициализация:

```bash
python scripts/production_context.py init
```

Импорт JSON bundle:

```bash
python scripts/production_context.py import data/production-context/example.json
```

Новый порядок bundle:

`sources → groups → projects → entities → entity_aliases → group_aliases → group_links → links → events → consultancies`.

Пример canonical identity:

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
  "groups": [
    {
      "group_id": "mcu",
      "kind": "shared_universe",
      "name": "Marvel Cinematic Universe"
    }
  ],
  "projects": [
    {
      "project_id": "tt1234567",
      "imdb_id": "tt1234567",
      "title": "Example Film",
      "release_at": "2026-07-01T00:00:00Z",
      "identity_known_at": "2025-06-01T00:00:00Z"
    }
  ],
  "entities": [
    {
      "entity_id": "marvel-studios",
      "kind": "studio",
      "name": "Marvel Studios",
      "external_id": "wikidata:Q434841"
    }
  ],
  "entity_aliases": [
    {
      "entity_id": "marvel-studios",
      "alias": "Marvel Studios, LLC",
      "known_at": "2025-06-01T00:00:00Z",
      "source_id": "src-1"
    }
  ],
  "group_aliases": [
    {
      "group_id": "mcu",
      "alias": "MCU",
      "known_at": "2025-06-01T00:00:00Z",
      "source_id": "src-1"
    }
  ],
  "group_links": [
    {
      "link_id": "tt1234567:mcu",
      "project_id": "tt1234567",
      "group_id": "mcu",
      "known_at": "2025-06-01T00:00:00Z",
      "source_id": "src-1",
      "installment_index": 3
    }
  ],
  "links": [
    {
      "link_id": "tt1234567:marvel-studios",
      "project_id": "tt1234567",
      "entity_id": "marvel-studios",
      "role": "studio",
      "stage": "production",
      "known_at": "2025-06-01T00:00:00Z",
      "source_id": "src-1"
    }
  ]
}
```

Combined factual + historical snapshot:

```bash
python scripts/production_context.py snapshot tt1234567 2026-01-15T00:00:00Z
```

Только historical aggregates:

```bash
python scripts/production_context.py history tt1234567 2026-01-15T00:00:00Z
```

Canonical resolution:

```bash
python scripts/production_context.py resolve-entity "Marvel Studios, LLC" --kind studio --cutoff 2026-01-15T00:00:00Z
python scripts/production_context.py resolve-group MCU --kind shared_universe --cutoff 2026-01-15T00:00:00Z
```

Доказательная timeline:

```bash
python scripts/production_context.py timeline tt1234567 2026-01-15T00:00:00Z
```

## Несколько режиссёров

Фильм может иметь полноценную режиссёрскую команду. Начиная со schema v11 API принимает `directors: [...]`, legacy `director` остаётся backward-compatible, а отдельные team-блоки используют все director-credit target-фильма.

Schema v15 дополнительно считает team-wide director↔writer и director↔actor histories по всей режиссёрской команде и всему principal cast. Production Context не дублирует эти IMDb-history признаки, а описывает надстройку производственного процесса.

## Студийная и франшизная модель производства

Для крупных франшиз и shared-universe проектов индивидуальный режиссёр может иметь меньше фактической автономии, чем в независимом кино. Это не означает автоматического ухудшения качества. Vanga проверяет только измеримые признаки: canonical production identity, объём прошлых проектов, повторное пересечение команды, изменения производства и внешние consultancies.

Marvel и другие крупные shared-universe студии должны быть обычными значениями общего контракта, а не специальным штрафом или исключением.

## «Тенденции», движения и культурные веяния

Vanga не должна кодировать политическую или культурную позицию как признак качества и не должна использовать чувствительные характеристики людей.

Вместо этого допустимо измерять конкретные производственные последствия, если они наблюдаемы и датированы:

- число внешних creative consultancies, когда оно документировано;
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
2. [x] Factual Production Context registry: projects/entities/provenance/events/consultancies + as-of snapshots.
3. [~] Canonical studio/producer/franchise/shared-universe identity + aliases + neutral historical counts реализованы; требуется реальное наполнение и проверка покрытия.
4. [~] Timestamped production-change registry — schema/store готовы, требуется наполнение воспроизводимыми данными.
5. [~] External creative consultancy registry — schema/store готовы, требуется наполнение воспроизводимыми данными.
6. [R] Исследование production-pressure / continuity proxies на temporal holdout.
7. [R] Rating-based studio/producer reputation — только после temporal outcome history.
8. [ ] Только после отдельных ablation — решение, какие Production Context признаки остаются в production model.

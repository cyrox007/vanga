# Production Context — контракт production-признаков

## Статус

Production Context отделён от IMDb training pipeline и CatBoost:

- `src/production_context.py` — factual registry и temporal snapshots;
- `src/production_identity.py` — canonical identity/aliases и count-based historical `as-of` aggregates;
- `src/production_continuity.py` — factual cross-project dependency/continuity;
- `src/production_changes.py` — derived factual production-change proxies;
- `src/production_consultancies.py` — neutral consultancy scope/stage/history context;
- `src/production_outcomes.py` — research-only outcome history;
- `scripts/production_context.py` — единый CLI;
- данные живут в отдельной `production_context.duckdb`.

Outcome ratings пока не подключены к CatBoost.

## Базовый принцип

Студия, продюсер, creative lead, франшиза, shared universe, cross-project dependency, production change или consultancy не являются автоматическим плюсом/минусом. Система хранит наблюдаемые факты и только после temporal ablation может использовать проверенный proxy.

## Temporal/provenance контракт

Для production-факта различаются:

- `event_at` — когда событие произошло;
- `known_at` — когда информация стала доступна наблюдателю;
- `source_id` — provenance с URL/date/confidence.

Pre-release snapshot использует только `known_at <= cutoff`.

Для historical context дополнительно требуется:

- prior project выпущен к cutoff;
- prior project выпущен раньше target release limit;
- target project исключён;
- future project исключён даже при заранее известной production identity/engagement.

## Registry

### Projects и identity

`production_projects` хранит project ID, optional IMDb ID, title, release date и identity timing. Canonical production entities: `studio`, `production_company`, `production_label`, `producer`, `creative_lead`, `consultancy`, `other`.

`project_entity_links` связывает entity с фильмом, role, stage, `known_at` и provenance.

Canonical groups поддерживают `franchise` и `shared_universe`; `project_group_links` хранит `known_at`, source, optional installment index.

Alias регистрируется явно с provenance. Техническая нормализация не выполняет fuzzy merge; неоднозначность требует canonical ID/kind.

### Production events

Поддерживаются `director_change`, `writer_change`, `creative_lead_change`, `release_date_change`, `rewrite`, `reshoot`, `recut`, `format_change`, `scope_change`, `production_label_change`, `other`.

Event хранит stage, `event_at`, `known_at`, source и structured details без quality label.

Для `release_date_change` derived слой использует `details.old_release_at/new_release_at`. Для `reshoot` additional photography может быть явно отмечена в `details.activity/subtype/shoot_type`.

## Cross-project continuity

`ProductionContinuityContext` хранит factual links между проектами: `sequel_of`, `prequel_of`, `spin_off_of`, `continues_story_from`, `crossover_with`, `requires_context_from`, `other`.

Scopes: story/character/world/continuity/other.

Прозрачные proxies:

- dependency count;
- prior released/future announced/unknown-release counts;
- downstream known/future counts;
- cross-project count;
- relation/scope counts;
- prior release span;
- known flag.

Это coordination/continuity load, а не quality sign.

## Derived production changes

`ProductionChangeContext` не дублирует raw event counts, а добавляет:

- `production_team_change_count`;
- `production_rework_count`;
- `production_additional_photography_count`;
- release delay count + total/max/mean days;
- release advance count + total/max days;
- release-shift coverage;
- event-date coverage;
- stage counts.

Generic release change без обеих дат не превращается в выдуманный delay/advance. Rewrite/reshoot/delay не получают автоматический отрицательный знак.

## Consultancy context

`ProductionConsultancyContext` описывает внешний consultancy context без оценки конкретной компании.

Текущий проект:

- distinct consultancy entity count;
- distinct `(entity, scope, stage)` context count;
- scope diversity;
- stage diversity;
- multi-scope consultancy count;
- per-scope/per-stage distinct entity counts.

История тех же canonical consultancies:

- `production_consultancy_history_known_ratio`;
- prior-project count mean/max;
- unique prior-project pool;
- отдельная same-scope history и coverage.

Consultancy без истории остаётся в denominator и в mean как ноль. Future projects и поздно раскрытые engagements не протекают в ранний cutoff.

Feature names не содержат названия consultancy, rating или quality signal. Подробности: `docs/PRODUCTION_CONSULTANCY_CONTEXT.md`.

## Factual snapshot

CLI `snapshot` объединяет:

- raw factual registry;
- count-based historical identity;
- continuity proxies;
- derived production changes;
- neutral consultancy context/history.

Research outcome ratings намеренно туда не входят.

## Historical identity

`ProductionIdentityHistory.history_features_as_of` считает только ранее выпущенные и известные production links:

- franchise/shared-universe prior project counts;
- role history coverage and prior-count mean/max;
- prior shared entity projects;
- repeated key-team projects;
- max shared entities.

Единый непрозрачный `production cohesion score` не вводится.

## Research outcome history

`ProductionOutcomeHistory.features_as_of` считает rating-агрегаты прошлых franchise/shared-universe/studio/producer/creative-lead identity.

Текущая IMDb `averageRating` — актуальный snapshot, а не значение рейтинга на историческом cutoff. Поэтому:

- `production_outcome_rating_point_in_time = 0`;
- CLI возвращает `research_only=true`;
- outcome features не входят в обычный snapshot;
- outcome features не подключаются к CatBoost;
- ML ждёт P7 rating history либо доказанный temporal target protocol.

Подробности: `docs/PRODUCTION_OUTCOME_HISTORY.md`.

## CLI

```bash
python scripts/production_context.py init
python scripts/production_context.py import data/production-context/example.json
python scripts/production_context.py snapshot PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py history PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py continuity PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py changes PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py consultancies PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py outcomes PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py timeline PROJECT_ID 2026-01-15T00:00:00Z
```

## Связь с Creative Team

Schema v11-v15 моделируют режиссёров, сценариста, актёров и их прошлые связи. Production Context добавляет слой студий/продюсеров/franchise/shared universe/events/consultancies/cross-project dependencies.

## Экспертные утверждения

Утверждение эксперта о корпоративном или культурном влиянии хранится как `expert_interpretation`. В pre-release Vanga может попасть только измеримый proxy, который был доступен до премьеры, имеет provenance, проходит отдельный temporal ablation и не ухудшает quality gate.

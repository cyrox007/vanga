# Production Context — контракт production-признаков

## Статус

Production Context отделён от IMDb training pipeline и CatBoost:

- `src/production_context.py` — factual registry и temporal snapshots;
- `src/production_identity.py` — canonical identity/aliases и count-based historical `as-of` aggregates;
- `src/production_outcomes.py` — research-only outcome history;
- `scripts/production_context.py` — `init/import/snapshot/history/outcomes/timeline/resolve-*`;
- отдельная `production_context.duckdb`;
- ML-интеграции outcome ratings пока нет.

## Базовый принцип

Студия, продюсер, creative lead, франшиза, shared universe или consultancy не являются автоматическим плюсом/минусом. Система хранит наблюдаемые факты и только после temporal ablation может использовать проверенный proxy.

## Temporal/provenance контракт

Для production-факта различаются:

- `event_at` — когда событие произошло;
- `known_at` — когда информация стала доступна наблюдателю;
- `source_id` — provenance с URL/date/confidence.

Pre-release snapshot использует только `known_at <= cutoff`.

Для historical identity дополнительно требуется:

- prior project выпущен к cutoff;
- prior project выпущен раньше target release limit;
- target project исключён;
- future project исключён даже при заранее известной production identity.

## Registry

### Projects

`production_projects` хранит `project_id`, optional IMDb ID, title, release date и identity timing. Legacy franchise/shared-universe поля остаются fallback для старых bundle.

### Production entities

Canonical entity kinds:

- `studio`;
- `production_company`;
- `production_label`;
- `producer`;
- `creative_lead`;
- `consultancy`;
- `other`.

`project_entity_links` связывает entity с фильмом, role, stage, `known_at` и provenance.

### Franchise / shared universe

`production_groups` поддерживает `franchise` и `shared_universe`.

`project_group_links` хранит canonical group, `known_at`, `source_id`, optional `installment_index` и note.

### Aliases

Entity/group alias регистрируется явно с provenance. Техническая нормализация делает Unicode NFKC/casefold и нормализует пунктуацию, но не выполняет fuzzy merge. Неоднозначный alias обязан завершаться ошибкой.

### Production events

Поддерживаются `director_change`, `writer_change`, `creative_lead_change`, `release_date_change`, `rewrite`, `reshoot`, `recut`, `format_change`, `scope_change`, `production_label_change`, `other`.

Event хранит stage, `event_at`, `known_at`, source и structured details без оценочного ярлыка.

### Consultancies

Consultancy scope: story/script/character/worldbuilding/authenticity/sensitivity/other. Название consultancy само по себе не является quality feature.

## Factual snapshot

`ProductionContextStore.features_as_of(project_id, cutoff)` возвращает только факты, известные на cutoff: franchise/shared-universe identity, installment, entity counts, production changes и consultancy counts/scopes.

## Historical identity

`ProductionIdentityHistory.history_features_as_of(project_id, cutoff)` считает только ранее выпущенные и известные production links.

Основные признаки:

- `production_franchise_prior_project_count`;
- `production_shared_universe_prior_project_count`;
- `production_<role>_history_known_ratio`;
- `production_<role>_prior_project_count_mean/max`;
- `production_prior_shared_entity_project_count`;
- `production_key_team_repeat_project_count`;
- `production_prior_shared_entity_max`.

Единый непрозрачный `production cohesion score` не вводится.

## Research outcome history

`ProductionOutcomeHistory.features_as_of(project_id, cutoff)` считает rating-агрегаты прошлых canonical production identity.

Для franchise/shared-universe и ролей studio/production_company/production_label/producer/creative_lead доступны:

- prior project count;
- prior rated project count;
- rating coverage;
- rating avg/median/std.

Для entity roles дополнительно есть `production_<role>_rating_history_known_ratio`.

`production_key_team_repeat_*` использует только прошлые фильмы, где повторялись минимум две текущие production entities.

Одинаковый prior film считается один раз независимо от числа совпавших entities.

### Ограничение rating-time

Текущая IMDb `averageRating` — актуальный snapshot, а не значение рейтинга на историческом cutoff. Поэтому outcome identity/release timing строгий, но rating number пока не point-in-time.

Из-за этого:

- `production_outcome_rating_point_in_time = 0`;
- CLI возвращает `research_only=true` и `rating_point_in_time=false`;
- outcome features не входят в обычный `snapshot`;
- outcome features не подключаются к CatBoost;
- production ML ждёт P7 rating history либо отдельный доказанный temporal target protocol.

Подробности: `docs/PRODUCTION_OUTCOME_HISTORY.md`.

## Missing-контракт

Prior project без IMDb rating остаётся в `prior_project_count`, но не входит в `prior_rated_project_count`, поэтому coverage уменьшается.

При полном отсутствии rated history numeric fallback `6.5` всегда сопровождается coverage=0/rated_count=0 и не трактуется как реальная репутация.

## CLI

```bash
python scripts/production_context.py init
python scripts/production_context.py import data/production-context/example.json
python scripts/production_context.py snapshot PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py history PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py outcomes PROJECT_ID 2026-01-15T00:00:00Z
python scripts/production_context.py outcomes PROJECT_ID 2026-01-15T00:00:00Z --imdb-db /path/to/imdb.duckdb
python scripts/production_context.py timeline PROJECT_ID 2026-01-15T00:00:00Z
```

## Связь с Creative Team

Schema v11-v15 моделируют режиссёров/сценариста/актёров и их прошлые связи. Production Context не дублирует их, а добавляет studio/producer/franchise/shared-universe/events/consultancies.

## Экспертные утверждения

Утверждение эксперта о корпоративном или культурном влиянии хранится как `expert_interpretation`. В pre-release Vanga может попасть только измеримый proxy, который был доступен до премьеры, имеет provenance, проходит отдельный temporal ablation и не ухудшает quality gate.

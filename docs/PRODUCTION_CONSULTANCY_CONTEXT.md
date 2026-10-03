# Production Consultancy Context

## Назначение

`src/production_consultancies.py` нормализует factual context внешних consultancy engagements и историю тех же canonical consultancy entities на ранее выпущенных проектах.

Слой намеренно **не** создаёт признаки вида «компания X хорошая/плохая» и не использует название consultancy как quality signal.

## Temporal-контракт

Текущий engagement видим только если `known_at <= cutoff`.

Исторический проект учитывается только если:

1. consultancy engagement на prior project был известен к cutoff;
2. prior project уже выпущен к cutoff;
3. prior project выпущен раньше target release limit;
4. target project исключён;
5. future project исключён, даже если consultancy engagement уже публично известен.

Поздно раскрытый consultancy engagement старого проекта появляется в истории только после своего `known_at`.

## Текущий consultancy context

Признаки:

- `production_consultancy_entity_count` — число distinct canonical consultancies;
- `production_consultancy_engagement_context_count` — число distinct `(entity, scope, stage)` контекстов;
- `production_consultancy_scope_diversity`;
- `production_consultancy_stage_diversity`;
- `production_consultancy_multi_scope_entity_count`;
- `production_consultancy_context_scope_<scope>_entity_count`;
- `production_consultancy_context_stage_<stage>_entity_count`.

Повторное подтверждение той же consultancy с тем же scope/stage не должно раздувать distinct context counters.

## Historical consultancy context

Для каждой текущей canonical consultancy считается число ранее выпущенных проектов.

Агрегаты:

- `production_consultancy_history_known_ratio`;
- `production_consultancy_prior_project_count_mean`;
- `production_consultancy_prior_project_count_max`;
- `production_consultancy_prior_shared_project_count` — unique pool прошлых проектов с любой текущей consultancy.

Consultancy без прошлой истории остаётся в denominator и в mean как ноль.

## Same-scope history

Отдельно проверяется не просто факт прошлой работы consultancy, а наличие опыта в scope, который используется на target project:

- `production_consultancy_same_scope_history_known_ratio`;
- `production_consultancy_same_scope_prior_project_count_mean`;
- `production_consultancy_same_scope_prior_shared_project_count`.

Например, прошлый опыт consultancy в `story` не считается автоматически опытом в `worldbuilding`, если target engagement заявлен только как `worldbuilding`.

## Что сознательно отсутствует

Нет:

- consultancy rating;
- quality score;
- polarity;
- имени компании в ML feature name;
- заранее заданного положительного/отрицательного коэффициента.

Название entity остаётся частью provenance/registry и может использоваться для identity resolution, но не является причинным признаком качества.

## CLI

```bash
python scripts/production_context.py consultancies PROJECT_ID 2026-01-15T00:00:00Z
```

Команда возвращает:

- нейтральные feature aggregates;
- deduplicated engagements, видимые на cutoff.

Обычный `snapshot` также включает consultancy-context features вместе с factual registry, production identity, continuity и production-change proxies.

## Путь к ML

Перед добавлением consultancy-признаков в CatBoost необходимо:

1. накопить воспроизводимый factual dataset;
2. убедиться в достаточном coverage;
3. сформировать только нейтральные scope/history proxies;
4. выполнить отдельный temporal ablation;
5. исключить признаки, ухудшающие quality gate;
6. не интерпретировать корреляцию как доказанную причинность.

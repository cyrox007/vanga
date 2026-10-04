# Actor Persona & Character Graph — реализация и испытания

## Статус

Actor Persona реализован как отдельный temporal-safe слой данных и candidate features. Он **не подключён автоматически к production CatBoost**: сначала требуется наполнить граф, провести temporal ablation и подтвердить пользу.

## Что хранится

`actor_persona.duckdb` содержит:

- источники и provenance;
- персонажей с внешними идентификаторами, franchise/universe context;
- появления `actor → work → character`;
- `known_at` и `work_release_at`;
- role function;
- meta-role type;
- genres/archetypes;
- признаки iconic/meta relevance и confidence.

Поддерживаемые meta-role типы:

- `ordinary`;
- `self_portrayal`;
- `fictionalized_self`;
- `explicit_persona_reference`;
- `iconic_character_cameo`;
- `parody_or_easter_egg`;
- `meta_ensemble_casting`.

## IMDb baseline

Историческую основу можно материализовать из локальной IMDb DuckDB:

```bash
.venv/bin/python scripts/actor_persona_imdb.py --limit 1000
```

Для полного прохода убрать `--limit`.

Materializer читает `title_principals.characters` и `title_basics`. Для безопасности `known_at` приравнивается к release year. Это означает: роль не становится доступной историческому persona snapshot раньше выхода фильма.

IMDb baseline намеренно **не угадывает**:

- что актёр играет самого себя;
- пасхалку/пародию;
- iconic status;
- archetype;
- что два одноимённых персонажа из разных работ являются одной сущностью.

Такие связи добавляются только из подтверждённой структурированной аннотации с provenance.

## CLI

```bash
# Инициализация
.venv/bin/python scripts/actor_persona.py init

# Статистика
.venv/bin/python scripts/actor_persona.py stats

# Persona snapshot на исторический cutoff
.venv/bin/python scripts/actor_persona.py snapshot nm0000001 \
  --cutoff 2025-01-01T00:00:00Z

# Candidate features конкретной будущей роли
.venv/bin/python scripts/actor_persona.py features role.json \
  --cutoff 2025-01-01T00:00:00Z

# Candidate ensemble features
.venv/bin/python scripts/actor_persona.py ensemble roles.json \
  --cutoff 2025-01-01T00:00:00Z
```

## Candidate features

Сейчас слой умеет вычислять:

- `role_versatility`;
- `role_persona_match`;
- `role_persona_inversion`;
- `iconic_character_reuse` при подтверждённой общей identity;
- `self_portrayal`;
- `fictionalized_self`;
- `explicit_persona_reference`;
- `meta_cast_member`;
- `meta_cast_density`;
- `cast_persona_synergy`;
- `known_history_ratio`.

Это исследовательские признаки. Они не означают автоматически «хорошо» или «плохо».

## Первый smoke-test

```bash
.venv/bin/python scripts/actor_persona_imdb.py --limit 10000
.venv/bin/python scripts/actor_persona.py stats
```

Затем взять несколько актёров с хорошо известной фильмографией и проверить snapshots на разных cutoff. Будущие роли не должны появляться в прошлом snapshot.

## Empirical gate

Перед включением признаков в ML:

1. собрать temporal dataset;
2. отдельно проверить обычные роли, повтор персонажа, self portrayal, meta cameo и persona inversion;
3. зафиксировать `unknown` там, где роль до релиза не раскрыта;
4. провести ablation на temporal holdout;
5. не публиковать признаки, ухудшающие MAE/калибровку либо создающие нестабильные объяснения;
6. проверить связь с Expert Corpus/StoryMap как объяснительный слой.

## Важное ограничение identity

IMDb baseline создаёт консервативную identity для конкретного work/actor/character slot. Он не склеивает одинаковые имена между фильмами. Cross-work identity должна приходить из отдельного подтверждённого источника или ручной структурированной разметки. Это защищает от ложного объединения одноимённых персонажей.

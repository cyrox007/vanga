# Production Outcome History — исследовательский слой

## Назначение

`src/production_outcomes.py` строит исторические outcome-агрегаты для canonical production identity, уже собранной в `production_context.duckdb`.

Слой отвечает на вопросы вида:

- как оценивались уже вышедшие прошлые проекты этой франшизы;
- как оценивались прошлые фильмы этого shared universe;
- какая история у текущих studio / production company / production label;
- какая история у текущих producer / creative lead;
- как оценивались прошлые проекты, где повторялись минимум две текущие production entities.

Он **не** делает причинного вывода «студия/продюсер/франшиза хорошая или плохая» и пока **не подключён к CatBoost**.

## Temporal-контракт

Production identity остаётся строго point-in-time:

1. target-link должен быть известен при `known_at <= cutoff`;
2. prior-link также должен быть известен при `known_at <= cutoff`;
3. prior project должен иметь `release_at <= cutoff`;
4. prior project должен быть выпущен раньше target release limit;
5. target project всегда исключается;
6. future project исключается даже если его production identity уже известна.

Поздно раскрытая связь старого фильма со студией/франшизой появляется в snapshot только после собственного `known_at`.

## Ограничение IMDb rating

Официальная текущая IMDb-выгрузка `title_ratings` не содержит timestamp конкретного значения `averageRating`.

Следовательно, если сегодня у фильма rating 7.4, мы не можем утверждать, что именно 7.4 был доступен наблюдателю несколько лет назад на историческом cutoff.

Поэтому outcome rating сейчас имеет специальный guard:

`production_outcome_rating_point_in_time = 0`

CLI также возвращает:

- `research_only: true`;
- `rating_point_in_time: false`.

До появления P7 rating history или отдельного доказанного temporal-протокола эти признаки не должны автоматически добавляться в production training.

## Feature contract

Для `franchise` и `shared_universe`:

- `production_<kind>_prior_project_count`;
- `production_<kind>_prior_rated_project_count`;
- `production_<kind>_prior_rating_coverage`;
- `production_<kind>_prior_rating_avg`;
- `production_<kind>_prior_rating_median`;
- `production_<kind>_prior_rating_std`.

Для ролей `studio`, `production_company`, `production_label`, `producer`, `creative_lead`:

- `production_<role>_prior_project_count`;
- `production_<role>_prior_rated_project_count`;
- `production_<role>_prior_rating_coverage`;
- `production_<role>_prior_rating_avg`;
- `production_<role>_prior_rating_median`;
- `production_<role>_prior_rating_std`;
- `production_<role>_rating_history_known_ratio` — доля текущих canonical entities этой роли, имеющих хотя бы один rated prior project.

Дополнительно:

- `production_identity_*` — unique pool прошлых проектов всех текущих production entities;
- `production_key_team_repeat_*` — только прошлые проекты, разделяющие минимум две текущие production entities.

Одинаковый прошлый фильм не получает дополнительный вес только потому, что в нём совпали несколько текущих entities: rating summary строится по unique prior projects.

## Missing-контракт

Фильм без IMDb rating остаётся в `prior_project_count`, но не входит в `prior_rated_project_count`.

Это уменьшает `prior_rating_coverage`.

Если реальной rating-history нет совсем:

- numeric fallback = `6.5` только для стабильного числового контракта;
- coverage = `0`;
- rated count = `0`;
- entity known ratio = `0`.

Таким образом fallback нельзя интерпретировать как фактическую среднюю репутацию.

## CLI

Исследовательский snapshot:

```bash
python scripts/production_context.py outcomes PROJECT_ID 2026-01-15T00:00:00Z
```

С другой IMDb БД:

```bash
python scripts/production_context.py outcomes PROJECT_ID 2026-01-15T00:00:00Z \
  --imdb-db /path/to/imdb.duckdb
```

Эта команда намеренно отделена от обычных `snapshot` и `history`, чтобы текущий IMDb rating случайно не воспринимался как строгий point-in-time production feature.

## Путь к production ML

Перед включением outcome-признаков в основную модель нужно:

1. накопить P7 rating snapshots или формально выбрать воспроизводимую long-term target policy;
2. построить настоящий point-in-time extractor;
3. проверить train/inference parity;
4. выполнить отдельный temporal ablation;
5. оставить только признаки, не ухудшающие quality gate;
6. проверить влияние на размер модели и VPS.

# P7 Rating Milestones

Этот слой превращает накопленную point-in-time историю IMDb rating в исследовательские post-release targets.

Он **не** является pre-release feature и не используется в prediction автоматически.

## Стандартные milestones

По умолчанию:

- `rating_early` — первая точка не раньше даты релиза, допустимый lag до 7 дней;
- `rating_30d` — первая точка не раньше `release + 30d`, lag до 7 дней;
- `rating_180d` — первая точка не раньше `release + 180d`, lag до 14 дней;
- `rating_long_term` — первая точка не раньше `release + 365d`, lag до 30 дней.

Точка **никогда** не берётся раньше target date.

## Missing/readiness semantics

Для каждого milestone различаются:

- `not_due_yet` — целевая дата ещё не наступила;
- `no_observation_after_target` — milestone уже наступил, но P7 не имеет подходящей точки;
- `observation_too_late` — точка есть, но лаг превышает контракт и она не может считаться target;
- `ready=true` — имеется наблюдение в допустимом окне.

Слишком поздняя точка сохраняется только как diagnostic candidate (`candidate_observed_at`, `candidate_lag_days`), но `average_rating/num_votes` target остаются `null`.

## Derived research deltas

Только если обе исходные точки готовы, вычисляются:

- `early_to_30d`;
- `30d_to_180d`;
- `180d_to_long_term`;
- `early_to_long_term`.

Это просто изменение rating, без автоматического вывода «хорошо/плохо» или причинности.

## CLI

```bash
python scripts/rating_milestones.py \
  tt1234567 \
  2026-01-15T00:00:00Z \
  --as-of 2027-02-01T00:00:00Z
```

## Почему нельзя заполнить 2025–2026 задним числом

Если Vanga не снимала point-in-time rating в прошлом, current IMDb rating не подменяет отсутствующий milestone. Старый фильм сможет получить только те milestones, для которых у нас реально есть наблюдения после запуска P7.

Таким образом P7 постепенно создаёт валидный датасет для исследований initial reception vs long-term reception, но не фальсифицирует исторические точки.

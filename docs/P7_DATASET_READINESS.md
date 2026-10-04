# P7 Dataset Readiness

`RatingMilestoneDatasetBuilder` отвечает на вопрос: достаточно ли уже накопленной point-in-time истории для исследования конкретного post-release target.

Он не запускает обучение и не превращает отсутствие данных в rating.

## Вход

Нужен список фильмов с однозначными `imdb_id` и `release_at`, а также **явный** `as_of`.

```json
{
  "cases": [
    {
      "case_id": "film-a",
      "imdb_id": "tt1234567",
      "release_at": "2026-01-15T00:00:00Z"
    }
  ]
}
```

Один IMDb id нельзя передать дважды с разными release contracts.

## Coverage

Для каждого milestone считаются:

- `case_count`;
- `due_count`;
- `ready_count`;
- `not_due_count`;
- `no_observation_count`;
- `too_late_count`;
- `due_ready_ratio` — coverage только среди уже наступивших milestones;
- `total_ready_ratio` — coverage относительно всего списка фильмов.

Фильм, для которого `rating_180d` ещё не наступил, не ухудшает `due_ready_ratio` этого target.

## Readiness thresholds

Порог не зашит в код как «магическое» число. Исследователь обязан указать его явно:

```bash
python scripts/rating_dataset.py report cases.json \
  --as-of 2027-01-01T00:00:00Z \
  --min-ready-rows 500 \
  --min-due-coverage 0.80 \
  --output report.json
```

Если thresholds не заданы, `ready_for_research_target=null`: отчёт показывает coverage, но не заявляет, что данных уже достаточно.

Даже при `ready_for_research_target=true` отчёт содержит `model_training_started=false`.

## Экспорт target dataset

Экспортируются только rows с `ready=true` и `usable_as_target=true`:

```bash
python scripts/rating_dataset.py export report.json rating_30d \
  --output rating_30d.json
```

Каждая строка содержит:

- `imdb_id` / `release_at`;
- target milestone и `target_at`;
- реальный `snapshot_id` / `observed_at`;
- `lag_days`;
- `average_rating` / `num_votes`;
- source fingerprint.

Report и каждый target export получают SHA-256 fingerprints, поэтому эксперимент можно привязать к точному состоянию накопленной истории.

## Граница P7

Этот слой — post-release research dataset. Он не является pre-release feature и не должен попадать в prediction model как вход.

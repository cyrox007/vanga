# P1 Data Coverage — статус

P1 разделяет фактическую историческую обеспеченность входных данных и числовой fallback, который старые поколения модели использовали для совместимости.

## Уже готово

- prior counts по режиссёру, сценаристу и актёрам;
- разделение `known_history`, `resolved_no_history`, `not_resolved`, `not_provided`;
- `known_people_ratio`, `resolved_people_ratio`, `missing_feature_count`;
- отдельный `model_familiarity`;
- рекомендация abstention при крайне слабой исторической обеспеченности;
- публичный UI на `jsint-site`: coverage, familiarity и причины низкой обеспеченности;
- candidate training/inference schema v6 с явными `director_known`, `writer_known`, `actor_N_known`;
- candidate schema v6 с `director_prior_count`, `writer_prior_count`, `actor_N_prior_count`;
- inference сохраняет реальный IMDb ID найденной персоны даже при нулевой prior history;
- train/inference используют одинаковый temporal cutoff: только работы до года прогнозируемого фильма;
- baseline-режим schema v5 на текущем pipeline без coverage-признаков;
- `--evaluation-only`: полный retrain/evaluation без изменения `models/current.json`;
- `scripts/coverage_ablation.py`: последовательное сравнение baseline v5 и candidate v6 на неизменной IMDb БД с JSON-отчётом;
- защита от случайной публикации baseline и от сравнения разных temporal datasets.

## Что ещё требуется до публикации schema v6

- выполнить smoke retrain на сервере;
- выполнить полный `scripts/coverage_ablation.py` на актуальной production IMDb БД;
- проверить MAE/RMSE/R², размер модели и JSON-отчёт;
- убедиться, что `comparison.non_regression_passed=true`;
- затем выполнить обычный full retrain schema v6;
- пропустить candidate через существующий quality gate;
- публиковать schema v6 только если ablation и quality gate не показывают недопустимое ухудшение.

## После публикации

Исследовательский следующий шаг P1 — coverage-aware uncertainty: проверить, действительно ли ошибка растёт в bins с низким `model_familiarity`, и только после этого расширять эмпирический диапазон или менять calibration.

Числовой fallback `6.5` пока сохраняется внутри feature vector для совместимости и как заполнение отсутствующего average. В schema v6 модель одновременно получает `*_known=0` и `*_prior_count=0`, поэтому может отличать отсутствие истории от настоящего среднего рейтинга около 6.5.
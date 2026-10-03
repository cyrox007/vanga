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
- train/inference используют одинаковый temporal cutoff: только работы до года прогнозируемого фильма.

## Что ещё требуется до публикации schema v6

- выполнить smoke retrain на сервере;
- выполнить полный temporal retrain schema v6;
- сравнить MAE/RMSE/R² и размер модели с активным поколением;
- провести ablation: baseline v5 против v6 coverage-признаков;
- пропустить candidate через существующий quality gate;
- публиковать schema v6 только если она не ухудшает установленный критерий качества.

## После публикации

Исследовательский следующий шаг P1 — coverage-aware uncertainty: проверить, действительно ли ошибка растёт в bins с низким `model_familiarity`, и только после этого расширять эмпирический диапазон или менять calibration.

Числовой fallback `6.5` пока сохраняется внутри feature vector для совместимости и как заполнение отсутствующего average. В schema v6 модель одновременно получает `*_known=0` и `*_prior_count=0`, поэтому может отличать отсутствие истории от настоящего среднего рейтинга около 6.5.
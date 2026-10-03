# P1 Data Coverage — статус

Первый шаг P1 реализует диагностический контракт покрытия данных без изменения feature schema опубликованной CatBoost-модели.

Готово:
- prior counts по режиссёру, сценаристу и актёрам;
- разделение `known_history`, `resolved_no_history`, `not_resolved`, `not_provided`;
- `known_people_ratio`, `resolved_people_ratio`, `missing_feature_count`;
- отдельный `model_familiarity`;
- рекомендация abstention при крайне слабой исторической обеспеченности.

Не считается завершённым:
- обучение новой schema с `*_known` / `*_prior_count` как признаками;
- temporal ablation и quality gate для этой schema;
- coverage-aware uncertainty.

До отдельного ML-инкремента внутренний числовой fallback старых поколений модели сохраняется только ради совместимости и не выдаётся в публичном coverage как фактическая история человека.
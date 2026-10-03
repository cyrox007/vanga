# P1 coverage changelog

- `PersonHistory` различает `known_history`, `resolved_no_history`, `not_resolved`, `not_provided`.
- В coverage добавлены `prior_count`, ratios, `missing_feature_count` и `model_familiarity`.
- Для крайне низкой familiarity возвращается `abstention.recommended=true`.
- Будущие работы по-прежнему исключаются из prior history.
- Числовой fallback опубликованной CatBoost schema не выдаётся через API как реальный historical average.
# P1 Data Coverage — первый шаг

Реализованный в этой ветке слой закрывает диагностическую часть P1: исторические counts, явные состояния unknown/resolved-no-history, ratios, model familiarity и abstention recommendation.

Он намеренно **не** объявляет P1 полностью завершённым: новая CatBoost schema с `*_known` / `*_prior_count` как обучаемыми признаками должна пройти отдельный temporal ablation и quality gate. До этого опубликованные поколения модели остаются совместимыми со старым числовым fallback внутри inference.
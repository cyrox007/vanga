# Data Coverage P1

P1 отделяет фактическую историческую обеспеченность входных данных от числового прогноза модели.

## Состояния персоны

Для режиссёра, сценариста и ключевых актёров API различает четыре состояния:

- `known_history` — персона найдена в IMDb и до года прогноза есть рейтинговая история в нужной роли;
- `resolved_no_history` — персона найдена, но до года прогноза нет подходящих прошлых работ;
- `not_resolved` — введённое имя не удалось сопоставить с локальным IMDb;
- `not_provided` — значение не передано.

`avg_rating` возвращается только для `known_history`. Отсутствие истории не должно выглядеть как реальный средний рейтинг 6.5.

## Поля coverage

`pre_release_profile.data_coverage` возвращает:

- `score` / `percent` / `level` — покрытие всего диагностического профиля, включая synopsis;
- `model_familiarity` — обеспеченность исторических person-сигналов rating-модели;
- `known_people_count`, `provided_people_count`, `known_people_ratio`;
- `resolved_people_count`, `resolved_people_ratio`;
- `missing_feature_count` — число из пяти ожидаемых исторических каналов команды без реальной prior history;
- `director`, `writer`, `actors[*].works_count` и совместимый alias `prior_count`;
- `abstention` — машиночитаемая рекомендация не трактовать числовой rating как надёжный при крайне низкой обеспеченности.

## Совместимость

P1 пока не меняет feature schema уже опубликованной CatBoost-модели. Старые поколения продолжают получать совместимые числовые значения внутри inference, но публичный диагностический контракт больше не выдаёт fallback за фактическую историю человека.

Следующий шаг — новая training/inference schema с явными `*_known` / `*_prior_count` признаками и temporal ablation перед публикацией поколения.
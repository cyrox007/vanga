# Data Coverage P1

P1 отделяет фактическую историческую обеспеченность входных данных от числового прогноза модели.

## Состояния персоны

Для режиссёра, сценариста и ключевых актёров API различает четыре состояния:

- `known_history` — персона найдена в IMDb и до года прогноза есть рейтинговая история в нужной роли;
- `resolved_no_history` — персона найдена, но до года прогноза нет подходящих прошлых работ;
- `not_resolved` — введённое имя не удалось сопоставить с локальным IMDb;
- `not_provided` — значение не передано.

`avg_rating` в публичном диагностическом контракте возвращается только для `known_history`. Отсутствие истории не должно выглядеть как реальный средний рейтинг 6.5.

## Поля coverage

`pre_release_profile.data_coverage` возвращает:

- `score` / `percent` / `level` — покрытие всего диагностического профиля, включая synopsis;
- `model_familiarity` — обеспеченность исторических person-сигналов rating-модели;
- `known_people_count`, `provided_people_count`, `known_people_ratio`;
- `resolved_people_count`, `resolved_people_ratio`;
- `missing_feature_count` — число из пяти ожидаемых исторических каналов команды без реальной prior history;
- `director`, `writer`, `actors[*].works_count` и совместимый alias `prior_count`;
- `abstention` — машиночитаемая рекомендация не трактовать числовой rating как надёжный при крайне низкой обеспеченности.

## Candidate schema v6

Training и inference умеют дополнительные числовые признаки:

- `director_known`, `writer_known`, `actor_1_known` … `actor_3_known`;
- `director_prior_count`, `writer_prior_count`, `actor_1_prior_count` … `actor_3_prior_count`.

Числовой fallback `6.5` для `*_avg_rating` пока остаётся внутри feature vector, чтобы не ломать старые поколения и CatBoost numeric input. В schema v6 он всегда сопровождается `known=0` и `prior_count=0`, поэтому модель может отличить отсутствие истории от реального среднего рейтинга около 6.5.

Inference сохраняет IMDb ID найденной персоны даже если prior history равна нулю. Это соответствует training, где categorical `*_id` известен независимо от наличия прошлых рейтингов.

## Smoke и непубликуемая оценка

Короткая проверка candidate schema v6:

```bash
python traning.py --smoke
```

Полное обучение и temporal evaluation **без публикации**:

```bash
python traning.py --evaluation-only
```

Воспроизвести baseline feature set schema v5 на текущей IMDb БД:

```bash
python traning.py --evaluation-only --without-coverage-features
```

Baseline запрещено публиковать через этот entrypoint: без `--evaluation-only` процесс завершится ошибкой до `save_trained_model`.

## Честный temporal ablation v5 → v6

Для решения о публикации coverage-признаков используйте отдельный сценарий:

```bash
python scripts/coverage_ablation.py
```

Он последовательно выполняет:

1. baseline v5 без `*_known` / `*_prior_count`;
2. candidate v6 с coverage-признаками;
3. проверку одинаковых train/test годов и числа строк;
4. проверку, что `imdb.duckdb` не изменилась между проходами;
5. сравнение MAE, RMSE, R², числа признаков и размера модели;
6. сохранение JSON-отчёта в `temp/ablation-reports/`.

Сценарий **не вызывает** `save_trained_model` и не меняет `models/current.json`. Если candidate превышает допустимую регрессию MAE, процесс возвращает код `2`.

Для отладочного сокращённого прохода можно передать `--iterations` и `--max-batches`, но решение о публикации принимается только по полному temporal ablation на всей актуальной выборке:

```bash
python scripts/coverage_ablation.py --iterations 1500 --batch-size 10000
```

Во время ablation нельзя параллельно запускать `ds_update`: сценарий обнаружит изменение файла IMDb БД и остановит сравнение.

## Публикация

После успешного полного ablation обычный запуск:

```bash
python traning.py
```

обучит candidate schema v6 и только затем передаст её в существующий quality gate. `models/current.json` переключается исключительно внутри успешного `save_trained_model`.

Coverage-aware uncertainty остаётся исследовательским следующим шагом: сначала нужно накопить фактические ошибки по `model_familiarity` bins и доказать, что низкое покрытие действительно требует другого диапазона.
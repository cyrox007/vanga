# Проверка готовности Vanga к релизу

`release_readiness.py` — единая локальная проверка перед production rollout. Она не загружает CatBoost второй раз и по умолчанию не обращается в интернет.

## Быстрая проверка

```bash
python scripts/release_readiness.py
```

Проверяются:

- версия Python;
- обязательные runtime-файлы и безопасный updater;
- доступность IMDb DuckDB и наличие `title_crew`/`title_writers`;
- `models/current.json`, активная generation, `model.cbm` и `metadata.pkl`;
- результат quality gate, если он записан в metadata;
- P9 `future_releases.duckdb`, schema и фактическое наполнение registry.

Exit code `0` означает отсутствие блокеров. Предупреждения не блокируют выкладку. Exit code `1` означает, что найден хотя бы один блокер.

## Строгая проверка P9

До наполнения future registry обычная проверка показывает предупреждение. Для релиза публичного каталога это состояние нужно считать ошибкой:

```bash
python scripts/release_readiness.py --strict-p9
```

## Live smoke локального API

После рестарта сервиса:

```bash
python scripts/release_readiness.py \
  --live-api \
  --strict-p9 \
  --markets DE,US,NL
```

Дополнительно проверяются:

- `/health`;
- `/model-info`;
- `/future/catalog` отдельно для каждого указанного ISO 3166-1 рынка.

Live-проверка обращается только к `--api-base-url`, по умолчанию `http://127.0.0.1:9100`. Внешние Wikidata/TMDb запросы во время readiness-check не выполняются.

## JSON для автоматизации

```bash
python scripts/release_readiness.py --live-api --strict-p9 --json
```

Формат содержит `ready`, массив `checks` и итоговые счётчики `pass/warn/fail`. Его можно использовать в deploy-скрипте или CI без разбора человекочитаемого текста.

## Что эта команда не делает

Readiness checker намеренно не:

- запускает retrain;
- меняет `models/current.json`;
- обновляет IMDb dataset;
- запускает collectors Wikidata/TMDb;
- изменяет P9 registry;
- проверяет `jsint-site` и его Celery процессы.

Эти действия остаются отдельными этапами production rollout. Проверка лишь делает итоговое состояние Vanga воспроизводимым и не позволяет принять отсутствие модели/БД/quality gate за успешный релиз.

## Рекомендуемый production-порядок

1. Обновить код Vanga безопасным updater.
2. Обновить IMDb dataset и пересобрать DuckDB.
3. Выполнить smoke retrain, затем полный retrain/ablation нужной schema.
4. Убедиться, что candidate прошёл quality gate и опубликована ожидаемая generation.
5. Наполнить/обновить P9 registry collectors-ами.
6. Запустить `release_readiness.py --strict-p9`.
7. Перезапустить `vanga.service`.
8. Запустить `release_readiness.py --live-api --strict-p9`.
9. После зелёной Vanga обновить `jsint-site` и выполнить его собственный smoke.

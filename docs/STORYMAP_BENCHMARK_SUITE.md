# P4 — StoryMap benchmark suite

## Назначение

Одиночный gold case полезен для отладки, но качество StoryMap matcher нужно измерять на наборе случаев. Suite runner запускает несколько cases одинаковым способом и считает агрегированные метрики без хранения исходных текстов произведений.

Suite состоит только из ссылок на структурированные JSON-файлы:

- canonical source StoryMap;
- canonical adaptation StoryMap;
- gold alignment;
- predicted alignment.

## Manifest version 1

```json
{
  "suite_id": "adaptation-alignment-v1",
  "version": 1,
  "cases": [
    {
      "case_id": "case-001",
      "split": "development",
      "source": "cases/case-001/source.json",
      "adaptation": "cases/case-001/adaptation.json",
      "gold": "cases/case-001/gold.json",
      "predicted": "cases/case-001/predicted.json"
    },
    {
      "case_id": "case-002",
      "split": "blind",
      "source": "cases/case-002/source.json",
      "adaptation": "cases/case-002/adaptation.json",
      "gold": "cases/case-002/gold.json",
      "predicted": "cases/case-002/predicted.json"
    }
  ]
}
```

Пути должны быть относительными и не могут выходить выше каталога manifest. Это делает suite переносимым и исключает случайное чтение посторонних файлов.

`case_id` и `split` в manifest обязаны совпадать с gold case.

## Запуск

Все splits:

```bash
python storymap_benchmark.py suite benchmark/manifest.json \
  --output benchmark/report.json
```

Только blind:

```bash
python storymap_benchmark.py suite benchmark/manifest.json \
  --split blind \
  --output benchmark/blind-report.json
```

Для финальной проверки semantic backend рекомендуется явно запускать `--split blind`, чтобы development cases не смешивались с отчётом blind validation.

## Micro и macro

Suite возвращает оба вида агрегации.

### Micro

Сначала суммируются TP/FP/FN всех cases, затем считаются precision/recall/F1.

Micro сильнее отражает большие cases с большим числом размеченных связей.

### Macro

Сначала метрики считаются для каждого case, затем усредняются.

Macro показывает, насколько backend стабилен между разными адаптациями и не маскирует плохой результат на небольшом case хорошим результатом на большом.

Обе метрики считаются:

- overall;
- по split;
- по StoryNode kind.

## Строгая проверка predicted map

До расчёта метрик suite runner проверяет принятые `maps_from`:

1. adaptation node с `maps_from` должен существовать в исходной adaptation map;
2. его `kind` не должен меняться;
3. каждый source key должен существовать в source map;
4. source и adaptation nodes должны иметь одинаковый `kind`.

Таким образом benchmark не может получить формально хороший результат из структурно недопустимой predicted map.

## Reproducibility fingerprint

Отчёт содержит два SHA-256:

- `manifest_fingerprint_sha256` — структура manifest и пути;
- `suite_fingerprint_sha256` — manifest плюс SHA-256 содержимого каждого source/adaptation/gold/predicted JSON.

Если меняется gold-разметка, StoryMap или prediction, content fingerprint меняется даже при неизменном manifest.

Также отчёт хранит `input_hashes`, чтобы можно было определить, какой конкретно файл изменился между двумя прогонами.

## Политика blind split

Код не может технически запретить человеку открыть gold JSON. Поэтому blind validation — процессный контракт:

- gold blind cases готовятся заранее;
- пороги/правила/backend фиксируются на train/development;
- затем запускается отдельный `--split blind`;
- после просмотра blind результатов нельзя продолжать называть тот же набор blind при дальнейшей подгонке: он становится development, а для следующей проверки нужен новый blind subset.

## Что дальше

После этого foundation можно начинать реальное наполнение benchmark cases и сравнивать:

- текущий deterministic baseline;
- lexical+structural candidates;
- embeddings;
- NLP pipeline;
- LLM extraction/matching;
- гибридные методы.

Ни один метод не должен автоматически записывать semantic match в `maps_from`, пока его precision/abstention policy не подтверждены на отдельном blind set.

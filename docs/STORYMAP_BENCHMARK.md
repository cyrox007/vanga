# P4 — StoryMap benchmark и candidate matching

## Зачем нужен benchmark

После baseline extractor, semantic enrichment и alignment нельзя продолжать добавлять эвристики только потому, что они выглядят разумно. Следующий слой должен измерять качество.

`src/story_benchmark.py` вводит два независимых контракта:

1. gold cases для source↔adaptation alignment;
2. candidate ranking, который предлагает возможные matches, но **никогда не записывает их в `maps_from` автоматически**.

## Gold case

Gold case хранит только структурную разметку и IDs StoryMap, поэтому не требует копирования полного текста сторонних произведений.

Пример:

```json
{
  "case_id": "example-001",
  "split": "blind",
  "source_map_id": "source:canonical",
  "adaptation_map_id": "film:canonical",
  "matches": [
    {
      "adaptation_key": "friend-combined",
      "source_keys": ["friend-a", "friend-b"],
      "note": "Два персонажа первоисточника объединены"
    },
    {
      "adaptation_key": "event-gate",
      "source_keys": ["source-event-gate"]
    }
  ]
}
```

Допустимые splits:

- `train` — можно использовать для разработки правила/backend;
- `development` — настройка порогов;
- `blind` — финальная проверка, результаты которой нельзя использовать для ручной подгонки до завершения прогона.

Gold match запрещает сопоставление разных `StoryNode.kind`.

## Метрики

`StoryAlignmentBenchmark` разворачивает many-to-one match в пары `(source_key, adaptation_key)` и считает:

- TP;
- FP;
- FN;
- precision;
- recall;
- F1.

Метрики возвращаются:

- overall;
- отдельно по каждому StoryNode kind.

Таким образом система получает штраф и за пропущенный source node в merge, и за лишний ложный `maps_from`.

## Candidate matcher

`StoryMatchCandidateGenerator` работает только с совместимыми `kind` и сейчас использует:

- token Jaccard;
- normalized sequence similarity;
- structural participant overlap через уже подтверждённые character matches.

Если adaptation event связан с персонажем, который уже надёжно сопоставлен с source character, source events с тем же персонажем получают дополнительный структурный сигнал. Это полезно даже когда RU/EN labels почти не имеют общих слов.

### Candidate score не является confidence

Score означает только **приоритет проверки кандидата**.

Результат всегда содержит:

```json
{
  "auto_accept": false
}
```

Даже candidate с максимальным score не меняет StoryMap и не попадает в `maps_from` без отдельного alignment/подтверждения.

Если два лучших кандидата отличаются меньше установленного gap, группа возвращается с `ambiguous=true`.

## CLI

Сгенерировать candidates:

```bash
python storymap_benchmark.py candidates \
  source-canonical.json film-canonical.json \
  --aligned character-aligned.json \
  --top-k 5 \
  --output candidates.json
```

`--aligned` необязателен. Из него берутся только однозначные `character` mappings с одним `maps_from`.

Оценить принятый alignment против gold:

```bash
python storymap_benchmark.py evaluate \
  source-canonical.json \
  film-canonical.json \
  gold.json \
  predicted-aligned.json \
  --output metrics.json
```

## Как формировать первый benchmark

Первый набор должен быть маленьким, но проверяемым вручную:

- несколько адаптаций с RU+EN summaries;
- cases с сохранёнными персонажами;
- merged characters;
- удалённые персонажи/события;
- reordered/compressed сюжетные элементы;
- worldbuilding/motivation relations;
- случаи, где похожий текст **не означает** один и тот же элемент.

Разметку следует делать независимо от candidate scores. Иначе benchmark будет подтверждать собственные эвристики.

## Следующий инкремент

После появления gold cases можно безопасно экспериментировать с semantic backend:

- embeddings;
- локальный NLP;
- LLM extraction/matching;
- комбинированный structural + semantic score.

Любой backend сначала возвращает candidates. Автоматическое принятие допускается только после измерения precision на blind set и отдельного явно заданного порога/abstention policy.

# P4 — Text → StoryMap extraction

## Назначение

P4 превращает RU/EN пересказы сюжета в канонический `StoryMap`, который уже умеет сравнивать существующий `StoryDiffAnalyzer`.

Первый extractor — `sentence-graph-baseline` v1. Это намеренно **не LLM и не претензия на полное понимание сюжета**. Его задача — дать воспроизводимый baseline с evidence/provenance, на котором можно измерять качество следующих NLP/LLM методов.

## Почему extractor отделён от StoryDiff

`src/story_diff.py` уже детерминирован: он сравнивает две канонические карты, учитывает `maps_from`, удалённые/добавленные/merged nodes и потерю causal/motivation relations.

Поэтому pipeline разделён:

1. text → raw StoryMap;
2. RU/EN merge внутри одной стороны;
3. source ↔ adaptation alignment и заполнение `maps_from`;
4. deterministic StoryDiff;
5. expert interpretation — только поверх observation/structural consequence.

Первый PR закрывает только шаг 1.

## Что извлекает baseline v1

Из каждого содержательного предложения создаётся `event` node.

Дополнительно extractor обнаруживает:

- повторяющиеся имена собственные;
- многословные имена даже при одном упоминании;
- явные motivation markers (`wants to`, `tries`, `decides`, `хочет`, `пытается`, `решает`, `должен` и т.п.);
- явные result-transition markers (`therefore`, `as a result`, `поэтому`, `в результате` и т.п.).

Он создаёт:

- `character` nodes;
- `event` nodes;
- `motivation` nodes;
- `relates_to` character↔event/motivation relations;
- `motivates` motivation→event relations;
- осторожные `causes` previous-event→current-event relations только для явного result transition.

`importance` baseline не пытается угадывать из текста: по умолчанию используется нейтральное значение. Confidence отражает только уверенность конкретной эвристики, а не качество фильма/сюжета.

## Evidence contract

Каждый node имеет хотя бы один evidence record:

- `source_id`;
- sentence index;
- character start/end offsets;
- locator;
- SHA-256 текста предложения;
- extractor method/version;
- confidence.

Raw excerpt отдельно в evidence не дублируется. Сам node label может содержать краткое предложение summary, потому что StoryMap без человекочитаемой подписи неудобен для проверки.

## Reproducibility

При одинаковых:

- input text;
- source ID;
- language;
- extractor version

получаются одинаковые node keys, map ID и evidence hashes.

Map ID включает SHA-256 входного текста. Изменение summary поэтому создаёт новый raw-map identity, а не молча заменяет старое наблюдение.

## Ограничения v1

Baseline не умеет надёжно:

- coreference (`он`, `она`, `they` → конкретный персонаж);
- морфологическую нормализацию русских имён;
- объединение `Анна` и `Анну`;
- распознавание тем и worldbuilding на semantic уровне;
- сложные причинные связи внутри предложения;
- bilingual entity identity;
- source↔adaptation matching.

Эти ограничения не скрываются: `metadata.alignment_status = not_aligned`, а весь результат помечается `research_only=true`.

## CLI

```bash
python storymap_extract.py summary.txt \
  --source-id wikidata:Q123:ruwiki:456 \
  --language ru \
  --output temp/storymap.json
```

Ограничить размер prototype extraction:

```bash
python storymap_extract.py summary.txt \
  --source-id example \
  --language en \
  --max-sentences 80
```

Если исходный summary содержит больше предложений, metadata явно возвращает `truncated=true`, `sentence_count_total` и `sentence_count_used`.

## Следующий P4-инкремент

Следующим отдельным слоем идёт **RU/EN canonical merge + source↔adaptation alignment**.

Он должен:

- не переписывать raw maps;
- хранить match evidence/confidence;
- поддерживать explicit aliases;
- сопоставлять characters/events/motivations только между совместимыми `kind`;
- заполнять `maps_from` в derived adaptation map;
- оставлять ambiguous matches неподтверждёнными;
- позволять затем запускать существующий deterministic `StoryDiffAnalyzer`.

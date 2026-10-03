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

Шаги 1–3 теперь имеют отдельные воспроизводимые слои; semantic extraction всё ещё остаётся исследовательской задачей.

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

## Ограничения baseline v1

Baseline не умеет надёжно:

- coreference (`он`, `она`, `they` → конкретный персонаж);
- морфологическую нормализацию русских имён;
- объединение `Анна` и `Анну`;
- распознавание тем и worldbuilding на semantic уровне;
- сложные причинные связи внутри предложения.

Эти ограничения не скрываются: весь результат помечается `research_only=true`/не используется как production feature без отдельной проверки.

## RU/EN canonical merge

`src/story_alignment.py` добавляет derived-слой `BilingualStoryMapMerger`. Raw RU/EN maps не изменяются.

Автоматическое объединение намеренно консервативное:

- exact normalized label разрешён для совместимых `kind`;
- простая RU→Latin transliteration автоматически применяется только к `character`;
- `event`/`motivation` с разным текстом на RU/EN **не считаются одним событием** только из-за похожести;
- для нетривиальных переводов имён или терминов используется explicit alias group.

Пример aliases JSON:

```json
{
  "aliases": [
    {
      "alias_id": "jon-snow",
      "kind": "character",
      "labels": ["Джон Сноу", "Jon Snow"],
      "canonical_label": "Jon Snow / Джон Сноу"
    }
  ]
}
```

Derived canonical map получает новые стабильные keys, а `merge_evidence` сохраняет исходные map/node IDs, язык, label, метод сопоставления и confidence.

## Source ↔ adaptation alignment

`SourceAdaptationAligner` сопоставляет уже канонические карты первоисточника и экранизации.

Автоматически допускаются:

- exact normalized label для одного `kind`;
- transliteration персонажей;
- только однозначный fuzzy-match имени персонажа выше жёсткого порога.

Если два source character дают почти одинаковый score, match не принимается и попадает в `ambiguous_matches`.

События и мотивации не получают fuzzy semantic match автоматически. Для них пока нужен exact label или explicit match после проверки человеком/следующим semantic extractor.

### Many-to-one / merge персонажей

Explicit mapping может связать несколько source nodes с одним adaptation node:

```json
{
  "matches": [
    {
      "adaptation_key": "friend-combined",
      "source_keys": ["friend-a", "friend-b"],
      "confidence": 0.95,
      "note": "Подтверждённое объединение персонажей"
    }
  ]
}
```

В derived adaptation map эти IDs записываются в `maps_from`. Существующий `StoryDiffAnalyzer` после этого автоматически выдаёт `change_type=merged` для соответствующих source nodes.

Explicit match между разными `StoryNode.kind` запрещён.

## Alignment evidence

Результат alignment содержит:

- `alignment_evidence` с методом/confidence каждого принятого match;
- `ambiguous_matches`, которые система отказалась принимать автоматически;
- `unmatched_source_keys`;
- `unmatched_adaptation_keys`;
- derived adaptation `StoryMap` с `maps_from`;
- готовый deterministic `story_diff`.

Этот слой также `research_only=true`: confidence описывает уверенность identity-match, а не художественное качество и не вероятность корректности всего StoryMap.

## CLI baseline extractor

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

## CLI bilingual merge / alignment

Объединить RU/EN raw maps одной стороны:

```bash
python storymap_align.py merge source-ru.json source-en.json \
  --map-id source:canonical \
  --aliases aliases.json \
  --output source-canonical.json
```

Построить source↔adaptation alignment и StoryDiff:

```bash
python storymap_align.py align source-canonical.json film-canonical.json \
  --matches confirmed-matches.json \
  --output aligned.json
```

`--aliases` и `--matches` необязательны. Без них применяются только консервативные автоматические правила.

## Следующие P4-инкременты

После identity/alignment foundation нужны отдельные измеримые улучшения:

1. semantic extractor для событий внутри предложения и coreference;
2. извлечение relationships/worldbuilding/themes/ending;
3. причинные связи `causes/motivates/explains/depends_on` не только по transition markers;
4. semantic candidate matching событий как **предложение**, а не автоматическая истина: confidence + ambiguity + evidence;
5. benchmark/blind set RU/EN summaries и метрики precision/recall по node/relation classes;
6. кеширование raw/derived StoryMap, чтобы не повторять тяжёлый анализ на малом VPS.

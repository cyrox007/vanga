# P4 — Structural transformations и lexical tone baseline

## Зачем нужен слой

`StoryDiffAnalyzer` уже надёжно фиксирует removed/added/merged элементы и потерю подтверждённых связей. Но для Adaptation Analyzer отдельно нужны понятия `compressed`, `rewritten`, `reordered` и dimension `tone`.

Этот слой намеренно **не делает semantic matching сам**. `src/story_transformations.py` работает только поверх уже подтверждённого source↔adaptation alignment (`maps_from`). Это защищает от ситуации, когда похожие labels ошибочно превращаются в вывод об изменении первоисточника.

Результат остаётся `research_only=true` и не используется production CatBoost.

## Compression

`compressed` появляется только когда один adaptation node имеет минимум два совместимых подтверждённых `maps_from`.

Пример:

```text
source event A + source event B
          ↓ confirmed maps_from
adaptation event X
```

Это более строгий сигнал, чем текстовое сходство. Два похожих события без alignment не считаются compression.

## Rewrite

Для one-to-one mapped nodes сравнивается neighbourhood подтверждённых структурных связей:

- `causes`;
- `motivates`;
- `explains`;
- `depends_on`;
- `relates_to`.

Если и в source, и в adaptation есть связи, но не менее половины объединённого набора signature изменилось, создаётся `rewritten` structural consequence.

Чистая потеря всех связей **не называется rewrite**: её уже корректно описывает существующий StoryDiff как relation loss. Это предотвращает двойную интерпретацию одного наблюдения.

## Reorder

`reordered` считается только для:

- `event` nodes;
- подтверждённого one-to-one alignment;
- минимум трёх сопоставленных событий.

Сравнивается относительный порядок событий в source/adaptation StoryMap. Для каждого события считаются инверсии порядка.

Пара из двух событий недостаточна для отдельного reorder-вывода: такой сигнал слишком хрупок.

## Tone baseline

`LexicalToneAnalyzer` не создаёт «оценку тона» и тем более оценку качества фильма.

Он считает только воспроизводимые частоты явных слов по одинаковым cross-language категориям:

- `threat`;
- `loss`;
- `hope`;
- `humor`;
- `affection`.

Для RU и EN используются отдельные словари, а результат нормализуется на 100 слов. Сохраняются:

- SHA-256 входного summary;
- число слов;
- marker counts;
- rates per 100 words;
- matched word ratio;
- `quality_sign = null`.

Сравнение двух профилей возвращает category deltas и L1 distance. Если маркерами покрыто менее 1% слов хотя бы на одной стороне, ставится `coverage_warning=true`.

Это только baseline для будущего benchmark. Он не должен использоваться как доказательство «мрачности», «хорошего тона» или качества адаптации.

## CLI

Structural transformations:

```bash
python storymap_transform.py transformations \
  source-canonical.json \
  film-aligned.json \
  --output temp/transformations.json
```

Lexical tone profile comparison:

```bash
python storymap_transform.py tone \
  source-summary.txt \
  film-summary.txt \
  --source-language ru \
  --adaptation-language en \
  --output temp/tone.json
```

## Следующая проверка

Benchmark suite должен получить gold cases с:

- many-to-one compression;
- подтверждённым rewrite relation structure;
- reordered events;
- ложными похожими labels, которые не являются match;
- независимой human annotation tone dimension.

Только после этого можно решать, нужен ли embeddings/LLM backend для каждого класса transformation.

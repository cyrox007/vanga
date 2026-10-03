# P4 — Semantic StoryMap enrichment

## Назначение

`src/story_semantics.py` — отдельный research-слой поверх `sentence-graph-baseline`.

Он **не заменяет** baseline и не переписывает raw StoryMap. На одинаковом summary сначала строится воспроизводимая baseline-карта, затем создаётся новый derived map с отдельными `method/version/evidence`.

Это важно для сравнения методов: позднее NLP/LLM-извлечение можно оценивать против того же raw baseline, не меняя исторические результаты.

## Что добавляет v1

Только узкие lexical rules с явным evidence:

- `worldbuilding` при явных маркерах вроде `magic/магия`, `rule/правило`, `prophecy/пророчество`, `kingdom/королевство`;
- `theme` только при явном упоминании темы (`explores the theme`, `исследует тему` и т.п.);
- `relationship`, если в одном предложении явно названы минимум два уже выделенных персонажа и присутствует relationship marker;
- `ending` только в последних двух предложениях и только при явном `in the end/в финале/...`;
- `because/потому что` → отдельный cause-event + `explains`;
- `only if/только если/depends on/зависит от` → condition-event + `depends_on`;
- `in order to/чтобы` → goal motivation + `motivates`;
- очень ограниченный coreference: если текущая фраза не содержит имени, содержит местоимение, а предыдущая фраза ссылалась ровно на одного персонажа, добавляется `relates_to` с низким confidence.

## Что v1 сознательно не делает

- не разрешает местоимение, если предыдущая фраза содержит двух или более персонажей;
- не делает общий semantic similarity событий;
- не определяет скрытые темы без явного lexical cue;
- не выводит worldbuilding только из жанра;
- не считает наличие/отсутствие этих признаков качеством фильма;
- не заменяет экспертную интерпретацию;
- не подмешивается в production CatBoost.

## Evidence

Каждый новый semantic node/relation получает:

- `source_id`;
- `sentence_index`;
- char offsets;
- locator;
- SHA-256 предложения;
- `method=rule-based-story-semantics`;
- `method_version`;
- конкретный `rule`;
- confidence.

Примеры rules:

- `worldbuilding_lexical_marker`;
- `theme_explicit_marker`;
- `relationship_marker_with_two_characters`;
- `because_clause`;
- `dependency_marker`;
- `explicit_goal_marker`;
- `single_recent_character_coreference`;
- `ending_explicit_marker`.

Confidence относится только к конкретному эвристическому наблюдению, а не к истинности полного сюжетного разбора.

## CLI

```bash
python storymap_semantic.py summary.txt \
  --source-id wikidata:Q123:ruwiki:456 \
  --language ru \
  --output temp/storymap-semantic.json
```

Ограничение prototype:

```bash
python storymap_semantic.py summary.txt \
  --source-id example \
  --language en \
  --max-sentences 80
```

## Место в pipeline

Для каждой стороны адаптации pipeline теперь может быть таким:

1. RU summary → raw baseline/semantic map;
2. EN summary → raw baseline/semantic map;
3. RU/EN canonical merge;
4. source ↔ adaptation alignment;
5. deterministic StoryDiff;
6. только затем expert observations/interpretations.

## Следующий шаг

Следующий P4-инкремент должен быть не ещё одним набором эвристик, а **benchmark + candidate semantic matcher**:

- небольшой blind/gold набор RU/EN summary pairs;
- подтверждённые character/event/motivation/relationship/worldbuilding matches;
- precision/recall по node classes и accepted alignment;
- semantic event candidates возвращаются как предложения с score/evidence;
- ambiguous candidates никогда не записываются в `maps_from` автоматически;
- только после измерения качества имеет смысл выбирать embeddings/LLM/NLP backend.

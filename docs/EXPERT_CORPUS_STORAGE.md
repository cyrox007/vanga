# P5 — хранение Expert Analysis Corpus

## Назначение

`src/expert_corpus.py` реализует структурированный retrospective corpus методов анализа экспертов. Он физически отделён от prediction model и по умолчанию хранится в `expert_corpus.duckdb` (`VANGA_EXPERT_CORPUS_DB`).

Corpus не используется как feature source для pre-release CatBoost. Его задача — находить проверяемые структурные закономерности, из которых позднее можно проектировать отдельные pre-release proxy и проверять их temporal ablation.

## Copyright-by-design

В schema **нет поля для полного текста обзора или транскрипта**.

Кроме того, API явно отвергает payload-поля:

- `text`;
- `full_text`;
- `transcript`;
- `full_transcript`;
- `review_text`;
- `review_transcript`.

Для стороннего материала сохраняются только:

- expert profile;
- title;
- public URL;
- media type;
- published/retrieved timestamps;
- наши короткие структурированные аннотации;
- timecode/section;
- ссылки на StoryMap/StoryDiff/Production Context при наличии.

Если когда-либо появится материал с отдельным разрешением/совместимой лицензией, его полный текст всё равно не следует добавлять в этот registry: для licensed source нужен отдельный content-storage contract.

## Сущности

### `expert_profiles`

Каждый эксперт — отдельный профиль:

- `expert_id`;
- `display_name`;
- `focus[]`;
- note.

Красный Циник и BadComedian не объединяются в один профиль или «средний вкус».

### `expert_cases`

Case относится к одному фильму/адаптации и хранит:

- case ID;
- IMDb ID, если известен;
- title/year;
- optional source work ID;
- split.

Splits:

- `train`;
- `development`;
- `blind`;
- `external_transfer`.

### `expert_materials`

Reference на публичный материал конкретного expert profile:

- URL;
- title;
- video/article/podcast/post/other;
- publication date;
- retrieved date.

### `expert_case_materials`

Явная связь материала с case. Claim нельзя добавить, пока material не связан с case: это предотвращает случайное смешивание разных фильмов.

### `expert_claims`

Claim хранит слои **раздельно**:

1. `claim_summary` — формализованный тезис;
2. `observation` — что наблюдается;
3. `structural_consequence` — какое структурное следствие из наблюдения предполагается;
4. `expert_interpretation` — оценочная интерпретация эксперта.

Также сохраняются:

- `dimension`;
- `change_type`;
- `timecode_or_section`;
- confidence нашей разметки;
- optional `story_map_id`;
- optional `story_diff_annotation_id`;
- tags.

Эти поля нельзя сворачивать в одну строку: observation не становится мнением, а мнение не становится автоматически структурным фактом.

### `expert_evidence`

Evidence хранится отдельными строками с polarity:

- `supporting`;
- `contradicting`.

Kinds:

- film/source summary;
- StoryMap;
- StoryDiff;
- Production Context;
- material reference;
- other.

Для StoryMap/StoryDiff/Production Context обязателен `reference_id`.

## Claim chain

`claim_chain(claim_id)` возвращает нормализованный объект:

`claim → evidence[] → contradicting_evidence[] → observation → structural_consequence → expert_interpretation`

Такой объект можно сравнивать с автоматическими StoryDiff observations, не принимая interpretation за ground truth.

## CLI

Создать schema:

```bash
python scripts/expert_corpus.py init
```

Импортировать bundle:

```bash
python scripts/expert_corpus.py import data/expert-corpus/bundle.json
```

Статистика:

```bash
python scripts/expert_corpus.py stats
```

Case summary:

```bash
python scripts/expert_corpus.py case CASE_ID
```

Claim chain:

```bash
python scripts/expert_corpus.py claim CLAIM_ID
```

## Bundle order

```text
profiles
→ cases
→ materials
→ case_materials
→ claims
→ evidence
```

Пример минимальной структуры:

```json
{
  "profiles": [
    {
      "expert_id": "red-cynic",
      "display_name": "Красный Циник",
      "focus": ["adaptation", "motivation", "worldbuilding"]
    }
  ],
  "cases": [
    {
      "case_id": "example-film",
      "film_title": "Example",
      "split": "development"
    }
  ],
  "materials": [
    {
      "material_id": "example-review",
      "expert_id": "red-cynic",
      "title": "Разбор Example",
      "source_url": "https://example.test/review",
      "media_type": "video",
      "retrieved_at": "2026-10-03T00:00:00Z"
    }
  ],
  "case_materials": [
    {
      "case_id": "example-film",
      "material_id": "example-review"
    }
  ],
  "claims": [
    {
      "claim_id": "claim-001",
      "case_id": "example-film",
      "material_id": "example-review",
      "dimension": "motivation",
      "change_type": "removed",
      "timecode_or_section": "12:34-13:10",
      "claim_summary": "Удалено объяснение решения персонажа",
      "observation": "Объясняющая сцена присутствует в source map и отсутствует в adaptation map",
      "structural_consequence": "Последующий переход теряет причинную поддержку",
      "expert_interpretation": "Эксперт считает поступок необоснованным",
      "confidence": 0.9,
      "story_diff_annotation_id": "auto-annotation-1"
    }
  ],
  "evidence": [
    {
      "claim_id": "claim-001",
      "polarity": "supporting",
      "evidence_kind": "storydiff",
      "description": "Автоматический StoryDiff фиксирует removed explaining element",
      "reference_id": "auto-annotation-1",
      "confidence": 0.95
    }
  ]
}
```

## Что дальше

Foundation ещё не измеряет agreement/disagreement экспертов. Следующий P5-инкремент должен добавить отдельный analysis layer:

- совпадение по dimension/change_type/structural consequence;
- disagreement без принудительного усреднения;
- StoryDiff support rate;
- leave-one-expert-out устойчивость;
- blind/external-transfer evaluation.

Только после этого найденные закономерности могут переходить в P6 как **гипотезы для pre-release proxies**, а не как прямые признаки из экспертных обзоров.

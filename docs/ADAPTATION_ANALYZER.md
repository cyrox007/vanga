# Adaptation Analyzer

`Adaptation Analyzer` — отдельный ретроспективный слой Vanga для анализа уже
вышедших экранизаций. Его задача — не предсказывать мнение конкретного критика,
а формализовать метод разбора: что изменилось относительно первоисточника, что
это изменило в структуре истории и как эксперт интерпретирует последствия.

## Почему слой отделён от Vanga

В анализе используются данные, которые появляются после премьеры: полный сюжет
фильма, сравнение с первоисточником и экспертные обзоры. Их нельзя напрямую
использовать в pre-release модели, иначе возникнет data leakage.

Поэтому:

- данные хранятся в отдельной `adaptation.duckdb`;
- все агрегированные признаки имеют префикс `retro_adapt_`;
- `imdb.duckdb` и inference Vanga к этой БД не подключаются;
- результаты нужны для исследования закономерностей и проектирования новых
  **pre-release** признаков, которые затем должны вычисляться только из данных,
  известных до премьеры.

## Три уровня разметки

### 1. Observation

Проверяемое наблюдение о различии первоисточника и экранизации.

```json
{
  "layer": "observation",
  "dimension": "worldbuilding",
  "change_type": "removed",
  "claim": "Из экранизации удалён блок устройства мира, объясняющий правила фракции.",
  "severity": 0.8,
  "confidence": 0.95
}
```

### 2. Structural consequence

Вывод о структурном следствии изменения.

```json
{
  "layer": "structural_consequence",
  "dimension": "motivation",
  "change_type": "removed",
  "claim": "После удаления контекста мотивация героя объяснена хуже.",
  "parent_id": "obs-lore",
  "severity": 0.7,
  "confidence": 0.8
}
```

### 3. Expert interpretation

Оценочное мнение критика. Оно хранится отдельно от наблюдаемого факта.

```json
{
  "layer": "expert_interpretation",
  "dimension": "worldbuilding",
  "claim": "Эксперт считает сокращение мира неудачным.",
  "source_id": "critic-red",
  "evidence_locator": "12:30-13:10",
  "parent_id": "obs-lore",
  "severity": 0.75,
  "confidence": 0.9,
  "polarity": -0.9
}
```

Так система не превращает субъективный вывод критика в объективный факт.

## Источники

Поддерживаются четыре типа:

- `film_summary` — пересказ сюжета экранизации;
- `source_summary` — пересказ первоисточника;
- `expert_review` — экспертный обзор;
- `other` — дополнительный источник.

RU и EN пересказы одного произведения хранятся как отдельные источники, но
считаются разными представлениями одной истории. Следующий extraction-слой
должен сводить их к общей канонической story map.

Для сторонних экспертных обзоров по умолчанию используется
`copyright_mode=reference`: сохраняются URL, автор, таймкод/раздел и собственная
структурированная аннотация, а не полный чужой текст. Полный текст допустим
только при наличии подходящей лицензии/разрешения (`licensed_text`).

## Каноническая story map и автоматический diff

В прототип уже входит `src/story_diff.py`. Он не пытается сравнивать русский и
английский текст по буквам. Сначала summaries должны быть сведены extraction-
процессом к каноническим узлам:

- `event`;
- `character`;
- `motivation`;
- `relationship`;
- `worldbuilding`;
- `theme`;
- `ending`.

И связям:

- `causes`;
- `motivates`;
- `explains`;
- `depends_on`;
- `relates_to`.

Минимальная карта:

```json
{
  "map_id": "source:demo",
  "language": "canonical",
  "nodes": [
    {
      "key": "lore-rule",
      "kind": "worldbuilding",
      "label": "Правило мира объясняет запрет",
      "importance": 0.9,
      "confidence": 0.95
    },
    {
      "key": "hero-motive",
      "kind": "motivation",
      "label": "Герой принимает запрет",
      "importance": 0.8,
      "confidence": 0.9
    }
  ],
  "relations": [
    {
      "kind": "explains",
      "source": "lore-rule",
      "target": "hero-motive",
      "importance": 0.85,
      "confidence": 0.9
    }
  ]
}
```

Если в карте экранизации `hero-motive` остаётся, а `lore-rule` исчезает,
`StoryDiffAnalyzer` автоматически создаёт:

1. `observation`: элемент worldbuilding удалён;
2. `structural_consequence`: связь `explains` больше не подтверждается.

То есть система уже умеет фиксировать класс ситуации «действие осталось, а
объясняющий его контекст исчез» без готового вердикта критика.

Для объединённых персонажей/линий узел экранизации может содержать:

```json
{
  "key": "friend-combined",
  "kind": "character",
  "label": "Объединённый персонаж",
  "maps_from": ["friend-a", "friend-b"]
}
```

Такой случай автоматически размечается как `merged`. Новые элементы
экранизации — как `added`, отсутствующие элементы первоисточника — как
`removed`.

CLI diff:

```bash
.venv/bin/python adaptation_analyze.py diff \
  --source-map temp/source-map.json \
  --adaptation-map temp/film-map.json \
  --output temp/story-diff.json
```

Полученные annotations совместимы с `AdaptationCase` и могут быть дополнены
экспертными интерпретациями.

## База данных

Переменная окружения:

```bash
export VANGA_ADAPTATION_DB=/home/projects/vanga/adaptation.duckdb
```

По умолчанию используется `adaptation.duckdb` рядом с проектом.

Таблицы:

- `adaptation_cases` — фильм и первоисточник;
- `adaptation_sources` — RU/EN summaries и ссылки на экспертные материалы;
- `adaptation_annotations` — observation / consequence / interpretation;
- `adaptation_feature_snapshots` — агрегированные retrospective-признаки.

## Быстрый старт

Создать БД:

```bash
.venv/bin/python adaptation_analyze.py init
```

Если фильм уже прошёл Wikimedia enrichment, создать JSON-черновик из RU/EN
пересказов фильма и `based_on` из Wikidata:

```bash
.venv/bin/python adaptation_analyze.py bootstrap \
  --imdb-id tt1234567 \
  --output temp/adaptation-tt1234567.json
```

После этого вручную или отдельным extraction-процессом добавить:

- RU/EN summary первоисточника;
- ссылки на экспертные обзоры;
- автоматически полученный structural diff;
- экспертные interpretations поверх наблюдаемых фактов.

Импорт:

```bash
.venv/bin/python adaptation_analyze.py import \
  --input temp/adaptation-tt1234567.json
```

Посмотреть рассчитанные признаки:

```bash
.venv/bin/python adaptation_analyze.py features \
  --case-id tt1234567:default
```

## Текущие retrospective-признаки

Прототип рассчитывает:

- severity по измерениям: plot, characters, motivation, relationships,
  worldbuilding, themes, tone, causal coherence, ending, format;
- экспертную polarity отдельно по каждому измерению;
- количество removed/added/merged/rewritten/compressed и других изменений;
- coverage структурной разметки;
- среднюю severity структурных изменений;
- среднюю polarity экспертных интерпретаций;
- долю экспертных выводов, которые привязаны к наблюдаемому факту/следствию;
- глубину цепочки `наблюдение → структурное следствие → интерпретация`.

Все ключи начинаются с `retro_adapt_`.

## Следующий этап

Теперь отсутствует в первую очередь не diff, а **text → story map extractor**.
Он должен объединять RU/EN plot summaries в одну смысловую карту и присваивать
стабильные canonical keys персонажам, событиям, мотивациям и элементам lore.
После этого существующий `StoryDiffAnalyzer` сможет автоматически покрывать
фильмы, для которых нет экспертного обзора.

Корпус хороших критических разборов нужен для проверки и улучшения ontology:
какие структурные последствия стоит искать, где автоматический diff ошибается и
какие типы изменений действительно связаны с итоговой реакцией аудитории.
Критик в таком контуре является учителем методики, а не целевой переменной.

# Полуавтоматическая разметка Expert Corpus

## Зачем

Ручной просмотр каждого обзора и набор JSON с нуля слишком дорог. `expert_corpus_autodraft.py` автоматизирует подготовительную часть, но **не объявляет машинно выбранные фрагменты gold-разметкой**.

Pipeline:

1. берёт URL материала из `expert_corpus.duckdb`;
2. для YouTube получает публичные обычные/автоматические субтитры через `yt-dlp`;
3. для статьи извлекает текстовые абзацы через `requests + BeautifulSoup`;
4. полный транскрипт после обработки не сохраняется в Expert Corpus;
5. выбирает потенциально аналитические фрагменты;
6. предлагает `dimension`, таймкод/раздел и короткий source fragment;
7. формирует annotation draft с обязательными маркерами `ЗАПОЛНИТЬ:`;
8. `validate/apply` не пропустят draft, пока человек не подтвердит или не исправит эти поля.

## Запуск

Список открытых train cases:

```bash
.venv/bin/python scripts/expert_corpus_annotate.py list --split train
```

Автоматически подготовить draft:

```bash
.venv/bin/python scripts/expert_corpus_autodraft.py pilot-bad-t34
```

По умолчанию файл создаётся здесь:

```text
data/expert/annotations/pilot-bad-t34.auto.json
```

Количество кандидатов можно ограничить:

```bash
.venv/bin/python scripts/expert_corpus_autodraft.py pilot-bad-t34 --max-candidates 12
```

После генерации нужно:

- удалить ложные кандидаты;
- проверить `dimension` и `change_type`;
- заменить каждый `ЗАПОЛНИТЬ:` на собственную структурированную аннотацию;
- не переносить в corpus длинные цитаты или полный транскрипт.

Проверка и применение:

```bash
.venv/bin/python scripts/expert_corpus_annotate.py validate data/expert/annotations/pilot-bad-t34.auto.json
.venv/bin/python scripts/expert_corpus_annotate.py apply data/expert/annotations/pilot-bad-t34.auto.json
```

## Защита blind/transfer

`blind` и `external_transfer` по умолчанию недоступны для autodraft, чтобы подготовительный процесс не подсматривал sealed gold.

Флаг `--allow-sealed` существует только для этапа **после фиксации prediction artifact**:

```bash
.venv/bin/python scripts/expert_corpus_autodraft.py <case_id> --allow-sealed
```

До этого момента использовать его нельзя.

## Ограничения первой версии

- YouTube должен иметь доступные ru/en subtitles или auto-subs.
- Для страниц с клиентским JavaScript простой HTTP collector может не увидеть закрытый/динамический текст.
- Выбор кандидатов пока детерминированный rule-based. Это намеренно: pipeline работает без внешнего AI API и результаты воспроизводимы.
- Следующее расширение может добавить опциональный LLM extractor, но только как генератор draft; human approval и sealed-split защита остаются обязательными.

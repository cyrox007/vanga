# P9. Канонизация территорий release dates

Этот документ фиксирует следующий обязательный шаг после добавления второго release-date source.

## Проблема

Один и тот же регион сейчас может иметь разные identity namespaces:

- Wikidata: `wikidata:Q30`;
- TMDb: `iso3166:US`.

Сравнивать такие строки напрямую нельзя, но и объединять их по display name небезопасно.

## Целевой контракт

Нужен локальный temporal-safe territory registry:

- `territory_id` — внутренний стабильный ID;
- `iso3166_1` — двухбуквенный код, если применим;
- `wikidata_id` — QID, если известен;
- `canonical_name` — только display value;
- `known_at`;
- `source_id` / provenance.

Release observation должна сохранять исходную identity источника и дополнительно иметь разрешённый canonical territory ID. Если mapping не доказан, observation остаётся unresolved.

## Правила

1. Нельзя сопоставлять территории только по названию.
2. ISO code и Wikidata QID связываются только через воспроизводимый factual source.
3. `worldwide` — отдельное логическое состояние, а не fallback для отсутствующего qualifier.
4. `unspecified` не равно `worldwide`.
5. Cross-source conflict считается только после успешной канонизации территории.
6. Исторический snapshot не должен видеть mapping, ставший известным позже cutoff.

После этого слоя Wikidata и TMDb release windows можно сравнивать по одной canonical territory и явно показывать agreement/conflict двух источников.

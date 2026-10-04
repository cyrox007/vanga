# P9. Региональная policy даты релиза

Этот слой подключает canonical territory resolver к future prediction и каталогу будущих фильмов.

## Главное правило

Публичный региональный сценарий обязан передавать целевую ISO 3166-1 территорию явно.

Примеры:

```text
US
DE
NL
iso3166:GB
```

Система нормализует их во внутренний storage key вида `iso3166:us`.

Отсутствующая региональная дата **никогда** не заменяется `worldwide` автоматически.

## `FutureRegionalReleaseService`

Сервис читает только локальную P9 DuckDB и объединяет:

- исходные release observations;
- temporal Wikidata QID → ISO mapping;
- независимые evidence из Wikidata, TMDb и других источников;
- metadata/team/status проекта.

Исходный provenance не переписывается.

### Resolution states

- `resolved` — для территории есть одно согласованное release window;
- `conflict` — источники дают разные release windows;
- `mapping_incomplete` — есть Wikidata territory observation, но её QID пока нельзя доказанно сопоставить с ISO territory;
- `missing` — региональной даты нет.

Для prediction точная дата доступна только когда согласованное окно имеет `precision=exact`.

## Региональный каталог

`catalog_as_of(..., territory=...)` фильтрует проекты по canonical территории и временному диапазону.

`worldwide` observation не делает проект частью регионального каталога сама по себе.

Это важно для будущего публичного UX: пользователь должен видеть даты для выбранного рынка, а не случайную мировую/фестивальную/локальную премьеру.

## `RegionalTemporalFuturePredictionPayloadBuilder`

Новый builder расширяет существующий temporal P9 builder и требует `territory`.

Он:

1. разрешает canonical regional release snapshot;
2. не использует implicit worldwide fallback;
3. объединяет Wikidata/TMDb только после territory mapping;
4. блокирует prediction при cross-source conflict;
5. блокирует prediction, если нет точной региональной даты;
6. продолжает использовать P9 temporal runtime/genres/synopsis;
7. остаётся полностью network-free на inference.

Новые blockers:

- `regional_release_date_conflict`;
- `regional_release_date_missing`;
- `regional_exact_release_date_missing`.

Warning:

- `regional_release_mapping_incomplete`.

## Совместимость

Старый `FuturePredictionPayloadBuilder` и `TemporalFuturePredictionPayloadBuilder` не меняются. Их существующий контракт сохранён для внутренних/legacy сценариев.

Новый региональный builder предназначен для дальнейшего публичного Vanga UX и новых integrations.

## Temporal safety

Canonical mapping применяется на том же cutoff, что и prediction.

Если соответствие `wikidata:Q... -> iso3166:...` стало известно позже cutoff, исторический snapshot его не видит. Это предотвращает leakage при backtest.

## Следующий шаг

После стабилизации этого контракта публичный jsint-site сможет явно выбирать/передавать рынок релиза и показывать:

- выбранную территорию;
- согласованную дату или окно;
- число независимых evidence;
- conflict/mapping-incomplete состояние;
- дату последнего доступного наблюдения.

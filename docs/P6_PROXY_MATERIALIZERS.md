# P6 — Source-specific proxy materializers

## Назначение

`Proxy Hypothesis Registry` фиксирует идею и temporal contract, а `TemporalProxyAvailabilityAuditor` проверяет готовую материализацию. Между ними нужен воспроизводимый слой, который получает значения proxy из уже существующих локальных хранилищ.

`src/proxy_materializers.py` материализует только явно поддержанные pre-release признаки из:

- IMDb historical data;
- `source_context.duckdb`;
- `production_context.duckdb`.

Слой **не обучает модель**, не меняет CatBoost schema и не принимает hypothesis. Его выход — обычный materialization JSON для P6 temporal audit.

## Target contract

Вход `targets.json`:

```json
{
  "targets": [
    {
      "target_id": "tt1234567",
      "target_year": 2025,
      "cutoff_at": "2025-02-01T00:00:00+00:00",
      "release_at": "2025-06-01T00:00:00+00:00"
    }
  ]
}
```

Опционально можно явно указать `source_project_id` и `production_project_id`. Иначе project ищется по `imdb_id`.

`cutoff_at >= release_at` считается ошибкой ещё до materialization.

## Source Context

Для `source_layer=source_context` materializer использует `SourceContextStore.features_as_of()` и одновременно проверяет физические строки, из которых появился feature.

Поддерживаются существующие source-context features:

- число связанных works/primary works/creators;
- source age/series metadata;
- source type/relation counts;
- planned runtime/episodes/total runtime;
- adaptation format one-hot.

### Missing != zero

Если на cutoff нет ни одного source link, `source_work_count` **не материализуется как 0**. Возвращается explicit missing row:

```json
{
  "available": false,
  "value": null,
  "missing_reason": "ни один source link не известен на cutoff"
}
```

То же правило действует для неизвестной publication date, series size/position и planned runtime.

Для format features temporal timestamp — `format_known_at`. Для source-link features используется фактический `known_at` видимых links; для creator count учитывается и creator-link `known_at`.

## Production Context

Для `source_layer=production_context` используются только facts, видимые через `known_at <= cutoff`.

Поддерживаются базовые factual features из `ProductionContextStore.features_as_of()`:

- franchise/shared-universe/installment identity;
- studio/production-company/label/producer/creative-lead counts;
- production change/event counts;
- consultancy presence/scope counts.

Если в registry нет ни одного видимого production event, `production_change_count` не превращается в фиктивный `0`. Это explicit missing. Аналогично для entity-role и consultancy groups.

Названия студий/consultancies не становятся feature values: материализуются только нейтральные counts/presence facts.

## IMDb history

Для `source_layer=imdb_history` базовый adapter поддерживает текущие role-history features:

- `director_prior_count`, `director_avg_rating`;
- `writer_prior_count`, `writer_avg_rating`;
- `actor_1..3_prior_count`, `actor_1..3_avg_rating`.

SQL всегда требует:

```text
prior.startYear < target_year
```

Same-year и future titles исключаются, даже если они уже находятся в свежем IMDb snapshot.

Если prior history отсутствует, materializer возвращает missing row вместо фиктивного исторического среднего.

### Adaptation-team history

Если preregistered feature совпадает с feature из `SourceTeamHistory`, materializer может использовать P3 adaptation-specific team history. Такой feature допускается только когда можно доказать `history_year < target_year`. Если в использованной истории есть same-year prior project, materialization должна быть отклонена/помечена missing — same-year нельзя маскировать более старым `history_year`.

## Provenance

Available row всегда содержит:

- `provenance_id`;
- либо `source_timestamp`, либо `history_year`;
- exact `source_layer`;
- exact `temporal_contract` из preregistered plan.

Materializer не меняет temporal contract на более удобный.

## CLI

```bash
python scripts/proxy_materialize.py \
  temp/worldbuilding-ablation-plan.json \
  temp/targets.json \
  --output temp/worldbuilding-materialization.json \
  --audit-output temp/worldbuilding-audit.json
```

Если audit запрошен:

- exit `0` — materialization и audit успешны;
- exit `3` — materialization создана, но temporal audit не прошёл;
- exit `2` — вход/contract/materializer невалиден.

## Ограничения

- `pre_release_public_signal` намеренно пока не материализуется автоматически: для него нужен отдельный датированный collector/provider.
- materializer не запускает CatBoost.
- поддержка source-specific feature должна быть явной; неизвестное имя feature вызывает ошибку, а не dynamic SQL/eval.
- current IMDb rating target никогда не является proxy feature.

## Следующий P6 шаг

Следующий слой — candidate training adapter:

1. читает **только успешно audited** materialization;
2. подмешивает proxy columns к существующему `creative_get_batches` по `tconst`;
3. создаёт coverage feature из `available/missing`, если он preregistered;
4. baseline и candidate обучаются на одной IMDb fingerprint/temporal holdout;
5. результаты проходят Generic Proxy Ablation Gate и Result Gate;
6. активная production model не меняется.

# P8 → P6 Audience Signal Materializer

P8 audience signals не могут попадать в candidate model напрямую. Они проходят тот же P6 temporal audit и ablation, что IMDb/Source/Production proxies.

## Canonical feature name

Protocol metadata является частью имени feature и, следовательно, preregistered plan fingerprint:

```text
audience__<signal>__<method>__<version>__<unit>__<field>
```

Пример:

```text
audience__trailer_views__youtube_public_stats__1__count__value
```

Допустимые fields:

- `value`;
- `known`;
- `age_days`;
- `coverage`;
- `sample_size`.

Компоненты protocol должны быть lowercase slug `[a-z0-9_]`. Автоматического выбора другой method/version нет.

## Missingness

Если exact protocol observation отсутствует:

- `value/age_days/coverage/sample_size` → explicit missing;
- `known` → доступное значение `0`, доказанное состоянием registry as-of cutoff.

Если observation есть, `known=1`.

Это не позволяет трактовать filler `0` как настоящий audience metric.

## Temporal contract

Adapter v1 принимает только:

```text
source_layer=pre_release_public_signal
temporal_contract=known_at_lte_cutoff
```

`published_at_lte_cutoff` не подменяется `known_at`: такой plan fail-closed до появления источника, который действительно хранит published timestamp.

## Unified materializer

`ProxyUnifiedMaterializer` поддерживает mixed plans:

- `imdb_history`;
- `source_context`;
- `production_context`;
- `pre_release_public_signal`.

Все rows попадают в один payload для `TemporalProxyAvailabilityAuditor`.

CLI:

```bash
python scripts/proxy_materialize_unified.py plan.json targets.json \
  --audience-db audience_signals.duckdb \
  --audit \
  --output materialization.json
```

Этот слой ничего не публикует и не меняет CatBoost. После audit всё равно требуется canonical P6 ablation gate.

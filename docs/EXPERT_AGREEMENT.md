# P5 — agreement/disagreement экспертов

## Зачем нужен этот слой

Expert Analysis Corpus не должен превращаться в «средний экспертный вкус».

`src/expert_agreement.py` сравнивает только структурную разметку:

`case_id + dimension + change_type`

и отвечает на вопросы:

- где два эксперта независимо отмечают один и тот же тип структурного изменения;
- где они расходятся;
- какие dimensions имеют устойчивое совпадение между несколькими профилями;
- сохраняется ли закономерность, если один эксперт исключён из сравнения.

Отчёт research-only и не является оценкой фильма, эксперта или качества прогноза Vanga.

## Silence policy

Отсутствие claim у эксперта **не является отрицательной меткой**.

Эксперты сравниваются только внутри `case_id + dimension`, которые реально размечены обоими. Если Красный Циник подробно разобрал worldbuilding, а другой эксперт эту dimension не обсуждал, это не считается disagreement.

По умолчанию учитываются только claims:

- с `confidence >= 0.5`;
- имеющие хотя бы одно `supporting` evidence.

Оба правила настраиваются явно.

## Pairwise report

Для каждой пары экспертов возвращаются:

- число общих `case+dimension` scopes;
- число точных совпадений набора `change_type`;
- exact agreement rate;
- mean Jaccard;
- scopes с `shared`, `only_left`, `only_right`.

Это симметричное сравнение. Здесь нет winner/loser и нет preference score.

## Consensus

Для каждого scope, размеченного минимум двумя экспертами, сохраняются:

- число экспертов;
- `unanimous`;
- число голосов по каждому `change_type`.

Consensus — это описание совпадения аннотаций, а не утверждение объективной истины.

## Leave-one-expert-out

Для каждого экспертного профиля строится отдельный отчёт:

1. текущий эксперт временно исключается;
2. для оставшихся экспертов внутри того же scope строится majority-набор change types;
3. разметка исключённого эксперта сравнивается с peer-majority;
4. считаются exact match rate и mean Jaccard.

Это позволяет увидеть, держится ли структурная закономерность без конкретного профиля, не смешивая вкусы в одну шкалу.

## Anti-leakage / privacy contract

Отчёт не содержит:

- `claim_summary`;
- `observation` text;
- `structural_consequence` text;
- `expert_interpretation`;
- полные тексты/транскрипты материалов.

Возвращаются только identifiers, display names и агрегированная структурная разметка.

## CLI

```bash
python scripts/expert_agreement.py \
  --split development \
  --min-confidence 0.5 \
  --output temp/expert-agreement.json
```

Для исследовательского прогона без требования supporting evidence:

```bash
python scripts/expert_agreement.py \
  --split development \
  --allow-without-supporting-evidence
```

## Следующий шаг

После наполнения корпуса реальными кейсами Красного Циника и BadComedian этот отчёт используется вместе с blind validation:

- blind validation показывает, находит ли Analyzer те же классы структурных проблем;
- agreement report показывает, какие классы устойчивы между независимыми экспертами;
- leave-one-expert-out помогает отсеять закономерности, завязанные на один профиль;
- только после этого можно проектировать pre-release proxy для P6.

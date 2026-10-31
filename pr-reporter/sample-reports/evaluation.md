# TestPilot F acceptance evaluation

Contract: f-acceptance-v1
Passed: True

Synthetic acceptance cases; not real-world accuracy.

| Metric | TestPilot | Coverage-presence heuristic |
| --- | --- | --- |
| precision | 1.0 | None |
| recall | 1.0 | 0.0 |
| specificity | 1.0 | 1.0 |

p95: 1.134 ms (30 samples). Scope: load A-D + score E + render F; excludes git, pytest and network.

Unknown labels excluded: 1.

## Acceptance checks

- precision_min: PASS
- recall_min: PASS
- specificity_min: PASS
- p95_ms_max: PASS
- uncertainty_disclosed: PASS

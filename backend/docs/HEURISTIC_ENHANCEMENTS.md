# Multi-Heuristic Strike Selector — `heuristicEnhancements`

Configure under `strategy_details_json.heuristicEnhancements` for `strategyType: heuristic-voting`.

## Enable / disable

- Omit the key → legacy behavior (all liquid strikes ranked, no post-processing).
- `"heuristicEnhancements": {}` → treated as disabled (`enabled: false`).
- Set `"enabled": true` with parameters below.

## Keys (JSON)

| Key | Default (in code) | Meaning |
|-----|-------------------|--------|
| `enabled` | `true` when object non-empty | Master switch |
| `maxMoneynessPct` | 1.2 | Skip strike if `|K-S|/S*100` exceeds unless raw heuristic score ≥ `moneynessOverrideMinScore` |
| `moneynessOverrideMinScore` | 4.5 | Override threshold for far strikes |
| `flatSpotBandPct` | 0.08 | Spot move % band → “flat” for joint table |
| `flatOiPct` | 0.5 | OI change % band → “flat” |
| `volumeHighRatio` | 1.5 | With flat OI → churn dampening |
| `oiChurnAbsPct` | 0.35 | \|OI chg\| below this + high vol → multiply score |
| `churnScoreMultiplier` | 0.94 | Churn dampening factor |
| `ltpStrongPct` | 2.0 | \|LTP chg\|% ≥ this → down-weight `oiChange` in scorer |
| `oiWeightWhenLtpStrong` | 0.45 | Factor on OI weight when LTP “strong” |
| `maxLtpOiCombinedWeightShare` | 0.88 | Max fraction of total weight on LTP+OI pair; `null` disables cap |
| `jointMinMult` / `jointMaxMult` | 0.72 / 1.08 | Clamp for spot×OI joint multiplier |
| `bestPerSideMinGap` | 0.35 | If #1−#2 < gap on a side, drop that side |
| `singleDirectionOnly` | false | Keep only stronger CE vs PE if spread ≥ `singleDirectionMinSpread` |
| `singleDirectionMinSpread` | 0.4 | Min score gap for single-direction mode |
| `ceRequiresSpotNotDown` | false | Skip CE when spot day chg < −`directionalGateFlatBandPct` |
| `peRequiresSpotNotUp` | false | Skip PE when spot day chg > +band |
| `directionalGateFlatBandPct` | 0.05 | Band for directional gates |
| `matrixOverrides` | — | Map `"near|ultra"` → `[true, 3.2]` = eligible + score cap |

## Optional CE/PE split

- `heuristicsCE` / `heuristicsPE` — same shape as `heuristics`; fallback to `heuristics`.
- `scoreThresholdCE` / `scoreThresholdPE` — optional; fallback to `scoreThreshold`.

## Update catalog row

From `backend/`:

```bash
python scripts/add_heuristic_strategy.py
```

Refreshes `strat-heuristic-voting` `1.0.0` including `heuristicEnhancements`.

## Implementation files

- `app/services/heuristic_enhancements.py` — matrices, joint table, best-per-side
- `app/services/heuristic_scorer.py` — LTP/OI decorrelation weights
- `app/services/trades_service.py` — `_get_live_candidates_heuristic` pipeline

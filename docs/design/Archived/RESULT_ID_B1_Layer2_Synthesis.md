# Layer 2 Synthesis — Algorithm Specification

**Version:** 1.0-draft | **Date:** 2026-03-06  
**Status:** Design session output. Pending architect confirmation of flagged assumptions.  
**Target:** Insert into `thread_2_signal.md` §11 (replaces §11.3 placeholder) after architect sign-off.

---

## Overview

Layer 2 consumes the structured outputs of the five EDSx pillar scorers (three horizons each) and
the five ML domain model outputs (14-day horizon), and produces the customer-facing composite
signals. This specification defines every formula, scale, and null-handling rule with sufficient
precision for unambiguous Python implementation.

**Input scales:**
- EDSx PillarScore: `score ∈ [0, 1]`. Centered form `c = 2×score − 1 ∈ [−1, 1]`.
- ML domain models: `p_bullish, p_neutral, p_bearish ∈ [0, 1]`, sum = 1.0.
- All synthesis intermediates and final composite: `[0, 1]`.
- Direction scalars: `[−1, 1]` (internal only; converted to [0, 1] before output).

---

## §L2.1 — Horizon Alignment

EDSx scores three horizons (1D, 7D, 30D). The ML track produces a single 14-day forecast.
Layer 2 emits three customer-facing composite signals.

| Signal Horizon | EDSx Input | ML Input | ML Weight Adjustment |
|---|---|---|---|
| **1D** | EDSx 1D composite | None | ML excluded (14D horizon incompatible with 1D signal) |
| **7D** | EDSx 7D composite | ML composite (14D) | Full weight (`w_ml_default`) |
| **30D** | EDSx 30D composite | ML composite (14D) | Discounted (`w_ml_30d = w_ml_default × 0.70`) |

**Rationale for 1D exclusion:** ML training labels are 14-day volume-adjusted returns. There is no
calibrated ML output at a 1-day horizon. The 1D signal is EDSx-only with track = `"edsx_only"`.

**Rationale for 30D discount:** The ML horizon (14D) is directionally consistent with 30D
structural positioning but is not calibrated to it. The 30% discount reflects horizon mismatch
without discarding the directional information entirely.

**Customer-facing labels:**

| Signal | Label | Semantics |
|---|---|---|
| `signals.1D` | Short-term | 24h tactical direction. EDSx only. |
| `signals.7D` | Medium-term | 7–14D forward. Primary synthesized signal. |
| `signals.30D` | Structural | 30D positioning. EDSx-dominant with ML cross-validation. |

**Python representation:**
```python
HORIZON_ML_WEIGHTS = {
    "1D":  0.00,   # ML excluded
    "7D":  1.00,   # full ML weight (w_ml_default applied)
    "30D": 0.70,   # 70% of w_ml_default
}
```

---

## §L2.2 — EDSx Pillar Aggregation

### Step 1: Select Regime Pillar Weights

Pillar base weights are a function of the active regime. During the legacy M2-only regime period,
use the three-regime table from §3.4. After VLA promotion (H2), use the VLA quadrant tables from
§L2.7 below.

### Step 2: Structural Risk Modulation

Structural Risk has architectural privilege (§3.1). Before any other adjustments:

```python
# s_sr = Structural Risk PillarScore.score ∈ [0, 1]
# threshold at 80th percentile = 0.80 in [0, 1] scale
SR_THRESHOLD = 0.80
SR_MAX_MULTIPLIER = 2.0

if s_sr is not None and s_sr >= SR_THRESHOLD:
    r = (s_sr - SR_THRESHOLD) / (1.0 - SR_THRESHOLD)   # r ∈ [0, 1]
    sr_multiplier = 1.0 + r                              # ∈ [1.0, 2.0]
    w_sr_new = w_sr_base * sr_multiplier

    # Redistribute excess proportionally from all other non-null pillars
    excess = w_sr_new - w_sr_base
    other_weight_sum = sum(w[p] for p in active_pillars if p != "structural_risk")
    for p in active_pillars:
        if p != "structural_risk":
            w[p] = w[p] * (1.0 - excess / other_weight_sum)
    w["structural_risk"] = w_sr_new
else:
    sr_multiplier = 1.0
```

### Step 3: Apply Guardrails Per Active Pillar

Evaluated on `confidence_base` per pillar per horizon. Applied in order G3 → G2 → G1.

```python
for pillar in active_pillars:
    cb = confidence_base[pillar][horizon]
    if cb < 0.10:                          # G3: zero weight
        w[pillar] = 0.0
        guardrails_applied.append(f"G3:{pillar}")
    elif cb < 0.30:                        # G2: clamp score + half weight
        score[pillar][horizon] = clamp(score[pillar][horizon], 0.30, 0.70)
        w[pillar] *= 0.5
        guardrails_applied.append(f"G2:{pillar}")
    elif cb < 0.50:                        # G1: half weight only
        w[pillar] *= 0.5
        guardrails_applied.append(f"G1:{pillar}")
```

### Step 4: Handle Null Pillars

A pillar is null if: (a) it is PLANNED and not yet built, (b) all sub-scores are null, or (c) G3 fired.
Null pillars receive `w[pillar] = 0.0`. This includes Valuation, Structural Risk, and Tactical Macro
in the v1 production state.

### Step 5: Renormalize

```python
total_w = sum(w[p] for p in active_pillars if w[p] > 0)

if total_w == 0.0:
    edsx_composite[horizon] = None
    edsx_null_reason[horizon] = "all_pillars_null_or_zeroed"
else:
    w_norm = {p: w[p] / total_w for p in active_pillars}
    edsx_composite[horizon] = sum(w_norm[p] * score[p][horizon]
                                  for p in active_pillars if w[p] > 0)
    edsx_confidence[horizon] = sum(w_norm[p] * confidence_base[p][horizon]
                                   for p in active_pillars if w[p] > 0)
    active_pillar_count[horizon] = sum(1 for p in active_pillars if w[p] > 0)
```

**Result:** `edsx_composite[horizon] ∈ [0, 1]`, or `None`.

**Null threshold:** If `total_w == 0` → EDSx composite = null for that horizon.

**Degradation flag:** `edsx_degraded[horizon] = (active_pillar_count[horizon] < 5)`

**v1 production note:** With Trend/Structure and Liquidity/Flow live and three pillars null,
this produces a valid two-pillar composite. `edsx_degraded = True`, `active_pillar_count = 2`.
This is expected and correct.

---

## §L2.3 — ML Model Aggregation

### Step 1: Classify Model Roles

The five ML domain models are divided into two roles:

| Model | Role | Direction Contribution |
|---|---|---|
| Derivatives Pressure | Directional | `d = p_bullish − p_bearish` |
| Capital Flow Direction | Directional | `d = p_bullish − p_bearish` (or `p_inflow − p_outflow`) |
| Macro Regime | Directional | `d = p_bullish − p_bearish` proxy via `risk_appetite` scalar |
| DeFi Stress | Directional (inverted) | `d = p_normal − p_critical` (DeFi stress is bearish) |
| Volatility Regime | Conditioner | Modulates weights of other four; does NOT contribute a direction |

**Macro Regime directional proxy:** The Macro Regime model emits `risk_appetite ∈ [−1, 1]`.
Use this directly as `d_macro = risk_appetite` (already a directional scalar; no conversion needed).

**DeFi Stress direction:** `d_defi = p_normal − p_critical`. Rationale: elevated or critical DeFi
stress is broadly bearish. p_elevated contributes neither bull nor bear — it is absorbed into the
neutral probability effectively.

```python
def model_direction(model_name: str, output: ModelOutput) -> float | None:
    if model_name == "derivatives_pressure":
        return output.p_bullish - output.p_bearish
    elif model_name == "capital_flow_direction":
        return output.p_inflow - output.p_outflow
    elif model_name == "macro_regime":
        return output.risk_appetite   # already ∈ [-1, 1]
    elif model_name == "defi_stress":
        return output.p_normal - output.p_critical
    elif model_name == "volatility_regime":
        return None  # conditioner only
```

### Step 2: Compute Per-Model Weights

Base weight = `feature_coverage`. Entropy discount applied using the linearized formula:

```python
MAX_ENTROPY_3CLASS = 1.5849625   # log2(3)

def entropy_discount(p_vec: list[float]) -> float:
    h = -sum(p * math.log2(p) for p in p_vec if p > 0)
    return 1.0 - 0.5 * (h / MAX_ENTROPY_3CLASS)
    # range: 0.50 (uniform = max entropy) to 1.00 (perfect certainty)

def model_weight(coverage: float, p_vec: list[float]) -> float:
    return coverage * entropy_discount(p_vec)
```

Note: Macro Regime uses `regime_probabilities` dict values as `p_vec`. DeFi Stress uses
`[p_normal, p_elevated, p_critical]`.

### Step 3: Volatility Regime Conditioning

The Volatility Regime model modulates the weights of the four directional models based on the
classified regime and `vol_direction`.

```python
VOL_REGIME_MULTIPLIERS = {
    #              deriv   flow   macro  defi
    "compressed": (1.20,   1.10,  0.90,  0.90),
    "normal":     (1.00,   1.00,  1.00,  1.00),
    "elevated":   (0.85,   0.90,  1.10,  1.15),
    "extreme":    (0.70,   0.80,  1.20,  1.20),
}

if vol_regime_output is not None:
    mults = VOL_REGIME_MULTIPLIERS[vol_regime_output.regime]
    w["derivatives_pressure"]  *= mults[0]
    w["capital_flow_direction"] *= mults[1]
    w["macro_regime"]           *= mults[2]
    w["defi_stress"]            *= mults[3]
# If vol_regime_output is None: no conditioning applied (default multipliers = 1.0)
```

### Step 4: Minimum Viable Model Count

The ML composite requires at least 2 active directional models. If fewer than 2 directional
models have non-null output and `w[model] > 0`: ML composite = null.

```python
DIRECTIONAL_MODELS = [
    "derivatives_pressure", "capital_flow_direction",
    "macro_regime", "defi_stress"
]

active_directional = [m for m in DIRECTIONAL_MODELS if output[m] is not None and w[m] > 0]

if len(active_directional) < 2:
    ml_composite = None
    ml_null_reason = "insufficient_active_models"
```

### Step 5: Compute ML Direction Scalar and Composite

```python
if ml_composite is not None:
    total_w = sum(w[m] for m in active_directional)
    d_weighted = sum(w[m] * model_direction(m, output[m])
                     for m in active_directional) / total_w
    # d_weighted ∈ [-1, 1]

    ml_composite = (d_weighted + 1.0) / 2.0   # map to [0, 1]
    ml_direction_scalar = d_weighted           # retained for provenance

    # ML composite coverage: mean feature_coverage of active models
    ml_coverage = sum(output[m].feature_coverage
                      for m in active_directional) / len(active_directional)

    # Degraded if fewer than 4 directional models active
    ml_degraded = len(active_directional) < 4
    active_model_count = len(active_directional)
```

**Result:** `ml_composite ∈ [0, 1]` or `None`. Degradation is flagged; synthesis weights are NOT
mechanically adjusted for partial ML model availability. The composite already reflects the
information content of active models.

---

## §L2.4 — Track-Level Agreement and Confidence Adjustment

Agreement is computed between the EDSx 7D composite and the ML composite (the two synthesis
partners). Agreement for 1D and 30D signals uses the same computation but substitutes the
appropriate EDSx horizon composite.

### Agreement Score

```python
def agreement_score(edsx: float, ml: float) -> float:
    """
    Returns agreement ∈ [-1, 1].
    +1 = maximum directional agreement (both at extremes, same direction)
    -1 = maximum disagreement (one fully bullish, one fully bearish)
     0 = one or both neutral
    """
    edsx_centered = edsx - 0.5   # ∈ [-0.5, 0.5]
    ml_centered   = ml   - 0.5   # ∈ [-0.5, 0.5]
    # Normalize product so maximum agreement = +1
    return (edsx_centered * ml_centered) / 0.25
```

### Confidence Boost / Penalty

```python
AGREEMENT_BOOST_FACTOR   = 0.15   # max +15% confidence when both tracks agree strongly
AGREEMENT_PENALTY_FACTOR = 0.20   # max −20% confidence when tracks strongly disagree
DISAGREEMENT_FLAG_THRESHOLD = -0.40  # flag when agreement_score < this value

agr = agreement_score(edsx_composite_h, ml_composite)

confidence_boost   = max(0.0, agr)  * AGREEMENT_BOOST_FACTOR
confidence_penalty = max(0.0, -agr) * AGREEMENT_PENALTY_FACTOR
disagreement_flag  = agr < DISAGREEMENT_FLAG_THRESHOLD
```

### Base Composite Confidence

```python
# Only computed when both tracks contribute.
# When single-track, composite_confidence = that track's confidence.
base_composite_confidence = (
    w_edsx_effective * edsx_confidence[horizon]
    + w_ml_effective  * ml_coverage
)

composite_confidence = clamp(
    base_composite_confidence + confidence_boost - confidence_penalty,
    0.0, 1.0
)
```

**Single-track fallback:** If EDSx composite is null, `composite_confidence = ml_coverage`.
If ML composite is null, `composite_confidence = edsx_confidence[horizon]`.

---

## §L2.5 — Final Composite Score and Direction Classification

### Synthesis Weights

```python
# Default weights (recalibrated quarterly)
W_EDSX_DEFAULT = 0.50
W_ML_DEFAULT   = 0.50

# Horizon adjustments (applied multiplicatively to W_ML_DEFAULT)
w_ml_horizon   = W_ML_DEFAULT * HORIZON_ML_WEIGHTS[horizon]
w_edsx_horizon = 1.0 - w_ml_horizon

# If one track is null: degenerate to single-track
if edsx_composite[horizon] is None and ml_composite is None:
    final_score[horizon] = None
    track[horizon] = "null"
elif edsx_composite[horizon] is None:
    final_score[horizon] = ml_composite
    track[horizon] = "ml_only"
    w_edsx_effective, w_ml_effective = 0.0, 1.0
elif ml_composite is None or HORIZON_ML_WEIGHTS[horizon] == 0.0:
    final_score[horizon] = edsx_composite[horizon]
    track[horizon] = "edsx_only"
    w_edsx_effective, w_ml_effective = 1.0, 0.0
else:
    final_score[horizon] = (
        w_edsx_horizon * edsx_composite[horizon]
        + w_ml_horizon  * ml_composite
    )
    track[horizon] = "synthesized"
    w_edsx_effective = w_edsx_horizon
    w_ml_effective   = w_ml_horizon
```

### Direction Classification

```python
BULLISH_THRESHOLD = 0.55
BEARISH_THRESHOLD = 0.45

def classify_direction(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= BULLISH_THRESHOLD:
        return "bullish"
    if score <= BEARISH_THRESHOLD:
        return "bearish"
    return "neutral"
```

---

## §L2.6 — Null Propagation Rules (Complete)

Every null state carries an explicit reason. Silent nulls are not permitted.

### EDSx Null Chain

| Condition | EDSx Composite Result | Reason Code |
|---|---|---|
| All pillars null or G3-zeroed | `None` | `all_pillars_null_or_zeroed` |
| 1 pillar active with G2 applied | Computed, `edsx_degraded=True` | `single_pillar_g2` |
| Pillar score itself null | Excluded via `w[p]=0` | propagated upstream |
| Sub-score null | Weight redistributed proportionally | (EDSx internal rule, §4.4) |

### ML Null Chain

| Condition | ML Composite Result | Reason Code |
|---|---|---|
| ML not graduated | `None` | `ml_not_graduated` |
| < 2 directional models active | `None` | `insufficient_active_models` |
| Model `feature_coverage < 0.20` | Model excluded (`w=0`) | `low_coverage_excluded` |
| Vol Regime null | No vol conditioning (multipliers default to 1.0) | — |
| Macro Regime null | Excluded from directional composite | `macro_regime_null` |

### Synthesis Null Chain

| EDSx | ML | Result | `track` |
|---|---|---|---|
| non-null | non-null | Synthesized formula | `"synthesized"` |
| non-null | null | EDSx only | `"edsx_only"` |
| null | non-null | ML only | `"ml_only"` |
| null | null | `None` | `"null"` |

**1D horizon:** ML is always excluded (`HORIZON_ML_WEIGHTS["1D"] = 0.0`), so 1D result is always
`"edsx_only"` or `"null"`. This is not degradation — it is the designed state.

---

## §L2.7 — Regime Weighting

### Legacy Regime: EDSx/ML Synthesis Weights

During the M2-only regime period, synthesis weights are fixed at 0.5/0.5. The regime affects only
EDSx pillar weights (§3.4). The 0.5/0.5 default applies regardless of whether regime is
risk_on, transitional, or risk_off.

### VLA Regime: Pillar Weights per Quadrant

When the VLA engine promotes to production, these weight matrices replace the legacy three-regime
tables. The legacy tables in §3.4 are superseded.

**Full Offense** (Expanding Liquidity + Low Volatility):

| Pillar | Weight | Rationale |
|---|---|---|
| Trend/Structure | 0.32 | Momentum maximally informative in trending, low-vol expansion |
| Liquidity & Flow | 0.28 | Flow confirmation in trending markets |
| Valuation | 0.12 | Valuations stretch; lower weight |
| Structural Risk | 0.08 | Base weight; vol is low, modulation unlikely |
| Tactical Macro | 0.20 | Monitor for macro reversal signal |

**Selective Offense** (Expanding Liquidity + High Volatility):

| Pillar | Weight | Rationale |
|---|---|---|
| Trend/Structure | 0.28 | Trend matters but vol creates noise |
| Liquidity & Flow | 0.22 | Flow divergences most readable |
| Valuation | 0.15 | Some mean-reversion opportunity in vol spike |
| Structural Risk | 0.15 | Elevated; watch for cascade proximity |
| Tactical Macro | 0.20 | Macro inflections drive vol events |

**Defensive Drift** (Contracting Liquidity + Low Volatility):

| Pillar | Weight | Rationale |
|---|---|---|
| Trend/Structure | 0.18 | Trend is fading; less weight |
| Liquidity & Flow | 0.18 | Flow out is the signal |
| Valuation | 0.22 | Value accumulation phase starting |
| Structural Risk | 0.25 | Elevated for defensive phase |
| Tactical Macro | 0.17 | Macro sets recovery timeline |

**Capital Preservation** (Contracting Liquidity + High Volatility):

| Pillar | Weight | Rationale |
|---|---|---|
| Trend/Structure | 0.12 | Bearish trend known; low incremental info |
| Liquidity & Flow | 0.12 | Confirms damage |
| Valuation | 0.20 | Recovery signal origin |
| Structural Risk | 0.45 | Dominates per architectural privilege at extremes |
| Tactical Macro | 0.11 | Macro set the stage |

### VLA Regime: EDSx/ML Synthesis Weights

Synthesis weights between tracks are also regime-dependent under VLA:

| Quadrant | w_edsx | w_ml | Rationale |
|---|---|---|---|
| Full Offense | 0.40 | 0.60 | Trending markets: Derivatives Pressure and Capital Flow models most reliable |
| Selective Offense | 0.50 | 0.50 | Default balanced (high uncertainty) |
| Defensive Drift | 0.55 | 0.45 | Structural signals more reliable than ML in slow defensive unwinding |
| Capital Preservation | 0.65 | 0.35 | Structural Risk EDSx pillar dominates; ML less reliable in crisis regimes |

**Application:** These replace `W_EDSX_DEFAULT` and `W_ML_DEFAULT` in §L2.5. The horizon
adjustment multipliers from §L2.1 are applied on top of the regime-adjusted defaults.

### VLA Quadrant Boundary Blending

At quadrant boundaries, sigmoid-blend adjacent weight sets to prevent whipsaw:

```python
def blend_weights(w_A: dict, w_B: dict, alpha: float) -> dict:
    """alpha ∈ [0, 1]: 0 = fully A, 1 = fully B"""
    return {k: (1.0 - alpha) * w_A[k] + alpha * w_B[k] for k in w_A}

def sigmoid_blend_alpha(distance_to_boundary: float, sharpness: float = 8.0) -> float:
    """distance_to_boundary: normalized [-1, 1], 0 = at boundary"""
    return 1.0 / (1.0 + math.exp(-sharpness * distance_to_boundary))
```

Boundary blending is applied when either VLA axis score is within `BLEND_ZONE = 0.15` of the
quadrant dividing line (0.50 on each axis).

---

## §L2.8 — Output Schema: /v1/signals Response (Per Instrument)

All fields nullable unless marked (required). Null fields must be present in the response with
explicit `null` value — omission is not permitted.

```python
SignalResponse = {
    # Identity
    "instrument_id":       str,             # required
    "as_of":               str,             # ISO 8601, required
    "synthesis_version":   str,             # "1.0", required

    # Regime context
    "regime":              str | None,      # current regime label
    "regime_source":       str | None,      # "legacy_m2" | "vla"
    "regime_quadrant":     str | None,      # VLA only: "full_offense" | ...

    # Per-horizon synthesized signals
    "signals": {
        "1D": {
            "score":               float | None,   # [0, 1]
            "direction":           str | None,     # "bullish"|"neutral"|"bearish"
            "confidence":          float | None,   # [0, 1]
            "track":               str,            # "synthesized"|"edsx_only"|"ml_only"|"null"
            "synthesis_weights": {
                "edsx":            float,          # 0.0–1.0
                "ml":              float,          # 0.0–1.0
            },
            "agreement":           float | None,   # [-1, 1]; null if single-track
            "disagreement_flag":   bool,
            "staleness_seconds":   int | None,     # age of oldest contributing data
        },
        "7D": { ... },   # same structure — primary synthesized signal
        "30D": { ... }   # same structure
    },

    # Magnitude (ML track only — Capital Flow Direction model)
    "flow_magnitude":      float | None,    # [0, 1]; null until ML graduates
    "magnitude_source":    str | None,      # "capital_flow_direction"

    # EDSx component (per horizon)
    "edsx": {
        "1D": {
            "composite":           float | None,   # [0, 1]
            "confidence":          float | None,
            "active_pillars":      int,            # 0–5
            "pillar_coverage":     float,          # active_pillars / 5
            "degraded":            bool,
            "pillars": {
                "trend_structure":  PillarDetail | None,
                "liquidity_flow":   PillarDetail | None,
                "valuation":        PillarDetail | None,
                "structural_risk":  PillarDetail | None,
                "tactical_macro":   PillarDetail | None,
            }
        },
        "7D": { ... },
        "30D": { ... }
    },

    # ML composite component (single 14D horizon)
    "ml": {
        "composite":               float | None,   # [0, 1]; null until graduated
        "direction_scalar":        float | None,   # [-1, 1]
        "active_models":           int,            # 0–4 directional models
        "model_coverage":          float,          # active_models / 4
        "degraded":                bool,
        "graduated":               bool,           # false until 30-day shadow passes
        "vol_regime_conditioning_applied": bool,
        "models": {
            "derivatives_pressure":  MLModelDetail | None,
            "capital_flow_direction": MLModelDetail | None,
            "macro_regime":          MLModelDetail | None,
            "defi_stress":           MLModelDetail | None,
            "volatility_regime":     MLModelDetail | None,
        }
    },

    # Provenance (immutable audit trail)
    "provenance": {
        "edsx_pillar_weights_used":       dict[str, float],
        "ml_model_weights_used":          dict[str, float],
        "structural_risk_modulation":     bool,
        "sr_multiplier":                  float,
        "guardrails_applied":             list[str],   # e.g. ["G1:liquidity_flow"]
        "vol_regime_multipliers_applied": dict[str, float] | None,
        "agreement_boost":                float,
        "agreement_penalty":              float,
        "null_inputs":                    list[str],
        "null_reasons":                   dict[str, str],
    }
}

PillarDetail = {
    "score":         float | None,   # [0, 1]
    "confidence":    float | None,   # [0, 1]
    "weight_used":   float,          # post-guardrail, post-modulation, renormalized
    "guardrail":     str | None,     # "G1" | "G2" | "G3" | None
    "null_reason":   str | None,     # null state code if score is null
    "freshness_seconds": int | None,
}

MLModelDetail = {
    "p_bullish":          float | None,
    "p_neutral":          float | None,
    "p_bearish":          float | None,
    "feature_coverage":   float | None,
    "prediction_entropy": float | None,
    "weight_used":        float,
    "null_reason":        str | None,
}
```

---

## Worked Example

**Scenario:** BTC, 2 live EDSx pillars (Trend/Structure + Liquidity/Flow), 3 ML model outputs
available (Derivatives Pressure, Capital Flow Direction, Volatility Regime), regime = Selective Offense.

Assumed inputs:

| Input | Value |
|---|---|
| Trend/Structure 7D score | 0.72 |
| Trend/Structure 7D confidence_base | 0.75 |
| Liquidity/Flow 7D score | 0.58 |
| Liquidity/Flow 7D confidence_base | 0.62 |
| Valuation, Structural Risk, Tactical Macro | null (PLANNED) |
| Derivatives Pressure: p_bull/p_neut/p_bear | 0.55 / 0.30 / 0.15 |
| Derivatives Pressure: feature_coverage | 0.78 |
| Capital Flow Direction: p_inflow/p_neut/p_outflow | 0.48 / 0.33 / 0.19 |
| Capital Flow Direction: feature_coverage | 0.65 |
| Volatility Regime: regime | "normal" |
| Macro Regime | null |
| DeFi Stress | null |

---

**Step 1: Regime → Pillar Weights**

Regime = Selective Offense (VLA) → base weights:
`{trend_structure: 0.28, liquidity_flow: 0.22, valuation: 0.15, structural_risk: 0.15, tactical_macro: 0.20}`

**Step 2: Structural Risk Modulation**

`s_sr = None` → no modulation. `sr_multiplier = 1.0`.

**Step 3: Null Pillars → Zero Weight**

Valuation, Structural Risk, Tactical Macro are null (PLANNED).
`w = {trend_structure: 0.28, liquidity_flow: 0.22, valuation: 0.0, structural_risk: 0.0, tactical_macro: 0.0}`

**Step 4: Guardrails**

confidence_base(Trend/Structure) = 0.75 → no guardrail  
confidence_base(Liquidity/Flow) = 0.62 → no guardrail

**Step 5: Renormalize**

`total_w = 0.28 + 0.22 = 0.50`  
`w_norm = {trend_structure: 0.56, liquidity_flow: 0.44}`

**Step 6: EDSx Composite 7D**

`edsx_7D = 0.56 × 0.72 + 0.44 × 0.58 = 0.403 + 0.255 = 0.658`  
`edsx_confidence_7D = 0.56 × 0.75 + 0.44 × 0.62 = 0.420 + 0.273 = 0.693`  
`active_pillar_count = 2`, `edsx_degraded = True`

---

**Step 7: ML Model Weights**

Derivatives Pressure:
```
entropy = -(0.55×log2(0.55) + 0.30×log2(0.30) + 0.15×log2(0.15))
        = -(0.55×(−0.862) + 0.30×(−1.737) + 0.15×(−2.737))
        = 0.474 + 0.521 + 0.411 = 1.406
entropy_discount = 1.0 - 0.5 × (1.406 / 1.585) = 1.0 - 0.443 = 0.557
w_deriv = 0.78 × 0.557 = 0.434
```

Capital Flow Direction:
```
entropy = -(0.48×log2(0.48) + 0.33×log2(0.33) + 0.19×log2(0.19))
        = -(0.48×(−1.059) + 0.33×(−1.600) + 0.19×(−2.396))
        = 0.508 + 0.528 + 0.455 = 1.491
entropy_discount = 1.0 - 0.5 × (1.491 / 1.585) = 1.0 - 0.470 = 0.530
w_flow = 0.65 × 0.530 = 0.345
```

**Step 8: Volatility Regime Conditioning**

`vol_regime = "normal"` → multipliers all 1.0. No conditioning effect.

**Step 9: Active Directional Models**

Macro Regime = null, DeFi Stress = null → excluded.  
Volatility Regime = conditioner only (no direction contribution).  
Active directional: [Derivatives Pressure, Capital Flow Direction] → count = 2 ≥ 2 (threshold).  
`ml_degraded = True`, `active_model_count = 2`

**Step 10: ML Direction Scalar and Composite**

```
d_deriv = 0.55 − 0.15 = 0.40
d_flow  = 0.48 − 0.19 = 0.29

total_w = 0.434 + 0.345 = 0.779
d_weighted = (0.434 × 0.40 + 0.345 × 0.29) / 0.779
           = (0.174 + 0.100) / 0.779
           = 0.274 / 0.779
           = 0.352

ml_composite = (0.352 + 1.0) / 2.0 = 0.676
ml_coverage = (0.78 + 0.65) / 2 = 0.715
```

**Step 11: Agreement**

```
edsx_centered = 0.658 − 0.5 = 0.158
ml_centered   = 0.676 − 0.5 = 0.176

agreement_score = (0.158 × 0.176) / 0.25 = 0.0278 / 0.25 = 0.111

confidence_boost   = 0.111 × 0.15 = 0.017
confidence_penalty = 0.0
disagreement_flag  = False  (0.111 > −0.40)
```

**Step 12: Synthesis Weights (7D, Selective Offense)**

Regime: Selective Offense → `W_EDSX_DEFAULT = 0.50`, `W_ML_DEFAULT = 0.50`  
Horizon: 7D → `HORIZON_ML_WEIGHTS["7D"] = 1.00`  
`w_ml_7D = 0.50 × 1.00 = 0.50`, `w_edsx_7D = 0.50`

**Step 13: Base Composite Confidence**

```
base = 0.50 × 0.693 + 0.50 × 0.715 = 0.347 + 0.358 = 0.704
composite_confidence_7D = clamp(0.704 + 0.017 − 0.0, 0, 1) = 0.721
```

**Step 14: Final Score 7D**

```
final_score_7D = 0.50 × 0.658 + 0.50 × 0.676 = 0.329 + 0.338 = 0.667
direction = "bullish"  (0.667 ≥ 0.55)
track = "synthesized"
```

**Step 15: 1D Signal (EDSx-only)**

`HORIZON_ML_WEIGHTS["1D"] = 0.0` → ML excluded by design.  
Assume EDSx 1D composite = 0.64, confidence = 0.71 (illustrative).  
`final_score_1D = 0.640`, `direction = "bullish"`, `track = "edsx_only"`

**Step 16: 30D Signal**

`w_ml_30D = 0.50 × 0.70 = 0.35`, `w_edsx_30D = 0.65`  
Assume EDSx 30D composite = 0.61, confidence = 0.68 (illustrative).  
`final_score_30D = 0.65 × 0.61 + 0.35 × 0.676 = 0.397 + 0.237 = 0.634`  
`direction = "bullish"`, `track = "synthesized"` (horizon-discounted ML weight)

---

**Summary Output:**

| Horizon | Score | Direction | Track | Confidence |
|---|---|---|---|---|
| 1D | 0.640 | bullish | edsx_only | 0.710 |
| 7D | 0.667 | bullish | synthesized | 0.721 |
| 30D | 0.634 | bullish | synthesized | 0.697 |

Flags: `edsx_degraded=True (2/5 pillars)`, `ml_degraded=True (2/4 models)`, `disagreement_flag=False`

---

## Open Assumptions Requiring Architect Confirmation

The following decisions are embedded in this specification. Each requires explicit
architect sign-off before the spec is locked and the Phase 2 build prompt is written.

**[A1] 1D horizon excludes ML entirely.**  
This spec treats the ML 14D forecast as incompatible with the 1D tactical signal and excludes it
completely. An alternative would be to include ML at a low weight (e.g., 10%) as a "tail context"
modifier even for the 1D signal. Confirm: ML excluded from 1D is the correct architectural choice.

**[A2] 30D ML discount factor = 0.70.**  
The 30% discount on ML weight for the 30D signal reflects horizon mismatch (14D ML vs. 30D EDSx).
The value 0.70 is an initial parameter. Confirm this is acceptable as a starting point, or specify
an alternative value.

**[A3] Minimum viable ML model count = 2 directional models.**  
Below 2 active directional models, ML composite is null and synthesis degrades to EDSx-only.
An alternative threshold (e.g., 3) would be more conservative. Confirm this threshold.

**[A4] Volatility Regime as conditioner only (no directional contribution).**  
This spec assigns Volatility Regime no direction score and excludes it from the ML composite
direction calculation. It only modulates other model weights. If the architect wants `vol_direction`
or `asymmetry` to contribute a directional component, the model aggregation logic must be revised.

**[A5] Volatility Regime conditioning multipliers.**  
The four regime multiplier sets in §L2.3 Step 3 are design priors, not empirically calibrated values:
`extreme: (0.70, 0.80, 1.20, 1.20)`, etc. These must be treated as initial configuration values and
validated against backtest data during Phase 4. Confirm acceptable as starting parameters.

**[A6] VLA EDSx/ML synthesis weights per quadrant.**  
Capital Preservation: 0.65/0.35. Full Offense: 0.40/0.60. Selective Offense: 0.50/0.50. 
Defensive Drift: 0.55/0.45. These are design priors. The quarterly recalibration mechanism will
update them empirically. Confirm acceptable as initial values.

**[A7] VLA pillar weight tables.**  
The four quadrant pillar weight tables in §L2.7 are initial design priors. They are consistent with
thread_2 §3.4 directional guidance ("Capital Preservation ≈ 45% Structural Risk"; "Full Offense
maximizes Trend/Structure and Liquidity/Flow"). Confirm these tables, particularly the Selective
Offense and Defensive Drift rows which are less explicitly constrained in the existing design.

**[A8] Agreement score formula uses centered product normalized to ±1.**  
The formula `(edsx_centered × ml_centered) / 0.25` correctly returns ±1 at maximum
agreement/disagreement between directional signals, and 0 when either track is neutral. An
alternative is absolute difference `1 − |edsx − ml|`. Confirm the product formulation is preferred
(it has the desirable property that two neutral signals score 0 agreement, not 1).

**[A9] Entropy discount formula: `1.0 − 0.5 × (H / H_max)`.**  
This is a gentle linear discount ranging from 1.0 (zero entropy) to 0.5 (maximum entropy). An
alternative would use a sharper nonlinear discount (e.g., exponential). The gentle form is chosen
to avoid over-penalizing models on mixed-signal days. Confirm or specify preferred discount form.

**[A10] Direction thresholds: bullish ≥ 0.55, bearish ≤ 0.45.**  
The neutral band of width 0.10 centered on 0.5 is a conservative choice that reduces false
directional labels when signals are weak. Widen (e.g., 0.60/0.40) for more selective signals;
narrow (e.g., 0.52/0.48) for more responsive labeling. Confirm thresholds, or specify per-horizon.

**[A11] `flow_magnitude` sourced exclusively from Capital Flow Direction model.**  
This is the most natural source (`flow_magnitude` field in `CapitalFlowOutput`). An alternative
would be a composite of flow_magnitude from Capital Flow and pressure_magnitude from Derivatives
Pressure. Confirm single-source attribution.

**[A12] Legacy synthesis weights are flat 0.5/0.5 regardless of legacy regime state.**  
Under the M2-only regime engine, synthesis weights do not vary by regime — only pillar weights
vary (§3.4). The VLA quadrant synthesis weight table (§L2.7) applies only after VLA promotion.
This decision is embedded in the spec. Confirm this is the intended separation.

---

*This document is a design session output. It carries no authority until architect confirms the
open assumptions above. Once confirmed, §L2.1 through §L2.8 replace the §11.3 placeholder in
`thread_2_signal.md` as the authoritative Layer 2 synthesis algorithm specification.*

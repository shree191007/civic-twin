# Civic-Twin — Changes to the Original Specifications

This document contains **only the changes and additions discussed after reviewing the original Civic-Twin specifications**. It is a patch/update, not a replacement for the original specification.

---

## 1. Reposition the Core Product

### Updated internal definition

> **A dependency-aware stochastic simulation engine for critical infrastructure resilience.**

### Updated public positioning

> **An AI-powered Critical Infrastructure Digital Twin that transforms hazard forecasts into cascading infrastructure risk, human impact, and actionable resilience decisions.**

### Core flow

```text
Forecast → Exposure → Damage → Cascade → Impact → Action
```

### Taglines

> **See the cascade before the disaster.**

> **Predict the hazard. Simulate the cascade. Protect the city.**

---

## 2. Do Not Build a Weather/Natural Disaster Prediction Model

Civic-Twin will **not solve meteorological forecasting or natural-disaster prediction from scratch**.

External systems/models can provide:

- Weather forecasts
- Extreme rainfall forecasts
- Flood forecasts
- Flood extent predictions
- Cyclone forecasts
- Wildfire intelligence
- Satellite observations
- Hazard probabilities

### Civic-Twin starts here

> **Given that this hazard is predicted or observed, what happens to the infrastructure system?**

---

## 3. Add a Hazard Ingestion Layer

```text
External Hazard / Weather Models
              ↓
       Hazard Ingestion API
              ↓
        Hazard Normalizer
              ↓
            Civic-Twin
```

Suggested module:

```text
hazard/
├── ingestion/
├── adapters/
├── normalization/
└── schemas/
```

Multiple providers should plug into adapters:

```text
Weather Model A ─────┐
Flood Model ─────────┤
Cyclone Model ───────┼──→ Hazard Normalizer → Civic-Twin
Satellite Data ──────┤
Wildfire Model ──────┘
```

### Common internal hazard schema

- `hazard_type`
- `probability`
- `start_time`
- `duration`
- `severity_distribution`
- `spatial_footprint`
- `uncertainty`
- `source`
- `provenance`

---

## 4. Use Hazard Uncertainty, Not Single Deterministic Predictions

Avoid treating this as unquestioned truth:

```text
Flood depth = 1.3m
```

Instead use distributions:

```text
50% probability → 0.4m–0.8m
30% probability → 0.8m–1.5m
20% probability → >1.5m
```

Updated flow:

```text
Hazard Forecast
      ↓
Probability Distribution
      ↓
Monte Carlo Scenario Sampling
      ↓
Infrastructure Cascade Simulation
      ↓
Impact Distribution
```

---

## 5. Add Forecast-to-Impact Intelligence

This becomes a central concept.

```text
FORECAST
   ↓
SIMULATE
   ↓
CASCADE
   ↓
QUANTIFY
   ↓
ACT
```

Expanded:

```text
Hazard probability
        ↓
Hazard scenarios
        ↓
Infrastructure dependencies
        ↓
Service + human impact
        ↓
Recommended intervention
```

---

## 6. Prevent Fake Precision

Do not produce overly precise outputs when assumptions are uncertain.

Avoid:

```text
CVaR = ₹42,731,829.42
```

Prefer:

```text
People affected:
85,000–120,000

Confidence:
MEDIUM

Primary uncertainty:
• Hazard intensity
• Asset vulnerability
• Restoration time
```

Outputs should include:

- Ranges
- Confidence levels
- Assumption provenance
- Sensitivity information
- Major uncertainty sources

---

## 7. Add an Assumption Ledger / Provenance Panel

Every important result should be explainable.

Example:

```text
WHY DOES CIVIC-TWIN THINK THIS?

• Substation flood vulnerability: Assumed
• Pump dependency: Verified topology
• Restoration time: Historical estimate
• Population exposure: External dataset estimate
```

Inputs should be classified as:

- Observed
- Verified
- Estimated
- Assumed
- Simulated
- Derived from an external model

---

## 8. Introduce Multi-Fidelity Simulation

Explicitly classify model fidelity.

### Tier 1: System topology

```text
Who depends on whom?
```

### Tier 2: Simplified engineering approximations

```text
What happens when dependencies degrade or fail?
```

### Tier 3: High-fidelity physical models

```text
Detailed physical behaviour
```

### MVP decision

Focus on:

> **Tier 1 + Tier 2**

---

## 9. Replace Binary Failure Logic with Infrastructure States

Do not model assets only as working/failed.

Use:

```text
OPERATIONAL
DEGRADED
BACKUP MODE
CRITICAL
FAILED
```

Example:

```text
Flood
  ↓
Substation degraded
  ↓
Power capacity falls
  ↓
Pump enters backup mode
  ↓
Backup resources decline
  ↓
Water pressure becomes critical
  ↓
Hospital service degrades
```

---

## 10. Add Time-Based Dependency Behaviour

Dependencies should include:

- Dependency type
- Required threshold
- Backup capacity
- Delay
- Degradation behaviour

Concept:

```text
Dependency(
    source,
    target,
    dependency_type,
    threshold,
    backup_capacity,
    delay
)
```

Example:

```text
Pump → requires → Power

Minimum power required: 60%
Backup duration: 4 hours
Failure delay: 30 minutes
```

Cascades occur over time:

```text
T+00: Hazard begins
T+10: Substation degraded
T+30: Pump enters backup mode
T+240: Backup exhausted
T+270: Water service collapses
T+300: Hospital capacity degrades
```

---

## 11. Handle Circular Dependencies

Do not assume the dependency graph is always a DAG.

Example:

```text
Power → Water Pump
Water → Power Cooling
```

Use:

- Discrete-time state transitions
- Event-driven simulation
- Fixed-point convergence

Concept:

```text
State(t)
   ↓
Dependency propagation
   ↓
State(t+1)
```

---

## 12. Add Effective Redundancy Score (ERS)

A signature metric.

### Problem

```text
Tower A ───┐
           ├── Shared Substation
Tower B ───┘
```

Nominal redundancy:

```text
2 towers
```

Effective independent redundancy:

```text
1 power dependency
```

Conceptually:

```text
ERS = Independent Failure Paths / Nominal Redundant Paths
```

Example UI:

```text
COMMUNICATION NETWORK

Nominal Redundancy:     2
Effective Redundancy:   1

⚠ HIDDEN SHARED DEPENDENCY DETECTED
```

---

## 13. Add Systemic Dependency Risk

Do not use graph centrality alone.

Conceptually:

```text
Systemic Dependency Risk =
Failure Probability
×
Failure Impact
×
Dependency Concentration
×
Recovery Difficulty
```

This identifies assets that may not be most likely to fail but would cause severe consequences if they do.

---

## 14. Change SPOF Detection to Counterfactual Testing

Do not define an SPOF simply as a high-centrality node.

For each asset compare:

```text
Baseline system
        vs
System with asset removed/degraded
```

Measure changes in:

- Service availability
- Population affected
- Recovery time
- Critical service capacity
- Total systemic loss

Concept:

```text
Impact(asset i) =
Loss(system without i)
-
Loss(normal system)
```

---

## 15. Use Multi-Objective Resilience Optimization

Do not optimize only economic loss.

Consider:

- People without essential services
- Critical service disruption
- Economic loss
- Recovery time

Possible UI:

```text
PRIORITY MODE

○ Protect Human Life
○ Minimize Economic Loss
○ Restore Services Fast
○ Balanced Strategy
```

---

## 16. Use Structured Scenario Families for Monte Carlo

Do not simply generate arbitrary random disasters.

Examples:

### River Flood

- Intensity
- Duration
- Spatial extent

### Urban Flood

- Rainfall intensity
- Drainage capacity
- Water accumulation

### Compound Event

```text
Flood + Power failure
```

Sample structured uncertainty:

```text
Hazard intensity
+
Asset vulnerability
+
Restoration uncertainty
+
Dependency failure
```

---

## 17. Separate Structural and Functional Importance

Distinguish:

### Structural importance

```text
Network topology
```

from:

### Functional importance

```text
Actual service loss if removed
```

This prevents SPOF detection from merely rediscovering obvious highly connected nodes.

---

## 18. Add Real Historical Flood Validation

Validation should include real flood events.

### A. Hazard validation

Compare predicted/supplied flood footprint with observed flood extent.

Possible metrics:

- IoU
- Precision
- Recall

### B. Infrastructure exposure validation

Test whether exposed infrastructure is correctly identified.

### C. Cascade validation

Compare predicted dependency cascades with documented disruptions.

Measure:

- Correct sectors identified
- Dependency chains identified
- Critical systems correctly ranked

### D. Decision counterfactual

Compare:

```text
Historical baseline
        vs
Same event + proposed intervention
```

Report this as:

> **Model-estimated counterfactual impact**

---

## 19. Add Historical Disaster Replay / Hindcasting

Evaluation workflow:

```text
Historical Flood Event
        ↓
Freeze available information before event
        ↓
Input historical hazard data
        ↓
Run Civic-Twin
        ↓
Compare predicted exposure/cascade
with documented reality
```

---

## 20. Keep Synthetic Ground-Truth Testing

Synthetic testing remains necessary.

Embed known hidden dependencies such as:

- Two towers sharing one substation
- Primary and backup fibre sharing one bridge
- Remote power-water-hospital dependency
- Repair depots isolated by flooding

Use these scenarios to evaluate:

- Hidden dependency detection
- SPOF detection
- Effective redundancy
- Intervention optimization

---

## 21. Explicitly Limit MVP Scope

### MUST BUILD

1. Infrastructure dependency graph
2. Hazard ingestion interface
3. Flood scenario integration
4. Cascade engine
5. Monte Carlo risk engine
6. Hidden dependency detection
7. Counterfactual SPOF analysis
8. Intervention optimizer

### SHOULD BUILD

1. Interactive map
2. Cascade timeline
3. Risk dashboard
4. Before/after intervention visualization
5. Assumption ledger

### DO NOT BUILD FOR MVP

- Full CFD flood simulation
- Detailed power-flow solver
- Full water hydraulics solver
- Custom meteorological foundation model
- GNN surrogate model
- Broad LLM infrastructure engineering system
- Full real-time city-wide ingestion

---

## 22. Clarify the Meaning of "Digital Twin"

For hackathon positioning, use:

> **AI-powered Critical Infrastructure Digital Twin**

Technically, the MVP should be described as:

> **A dependency-aware stochastic infrastructure resilience simulator.**

A continuously synchronized real-time digital twin remains a future production direction.

---

## 23. Updated Architecture

```text
EXTERNAL HAZARD MODELS
        │
        ▼
HAZARD INGESTION + NORMALIZATION
        │
        ▼
INFRASTRUCTURE ONTOLOGY
        │
        ▼
EXPOSURE + DAMAGE ENGINE
        │
        ▼
TIME-BASED CASCADE ENGINE
        │
        ├──────────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼
      Power      Water      Comms    Transport
        │          │          │          │
        └──────────┴──────────┴──────────┘
                     │
                     ▼
               SERVICE IMPACT
                     │
                     ▼
                HUMAN IMPACT
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
     RISK ENGINE          SPOF ENGINE
          │                     │
          └──────────┬──────────┘
                     ▼
          RESILIENCE OPTIMIZER
                     │
                     ▼
           DECISION DASHBOARD
```

---

## 24. Updated Demo Story

```text
1. Hazard forecast or historical flood detected
        ↓
2. Flood footprint enters Civic-Twin
        ↓
3. Critical infrastructure exposure identified
        ↓
4. First infrastructure failure occurs
        ↓
5. Dependencies propagate failure over time
        ↓
6. Hidden systemic dependency is discovered
        ↓
7. Human/service impact is quantified
        ↓
8. Optimizer recommends interventions
        ↓
9. Before/after systemic risk is shown
```

Key reveal:

> **The asset was not necessarily the problem. The hidden dependency was.**

---

## 25. Updated Product Positioning

### Main tagline

> **See the cascade before the disaster.**

### One-line explanation

> **Civic-Twin turns hazard forecasts into infrastructure intelligence.**

### Hackathon pitch

> **Civic-Twin is an AI-powered critical infrastructure digital twin that integrates hazard forecasts, simulates cascading failures across interconnected systems, quantifies human and service impact, and recommends resilience interventions before disaster strikes.**

### Technical description

> **A dependency-aware stochastic simulation engine that transforms probabilistic hazard information into systemic infrastructure risk and decision support.**

---

# Final Principle

Do not claim:

> **We perfectly predict disasters and simulate an entire city.**

The stronger claim is:

> **Given uncertain information about a hazard, Civic-Twin helps decision-makers understand how infrastructure dependencies can transform that hazard into a systemic crisis, and where interventions can reduce catastrophic risk.**

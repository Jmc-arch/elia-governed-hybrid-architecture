# ELIA — Development Guide 

> **Purpose:** This document explains how the ELIA project transitions from architecture to code, what must be implemented first, and why this order matters.
>  
> This is not a feature list. It is a systems strategy.

---

## 1. Development Philosophy

ELIA is not a traditional LLM agent project.

Most AI projects start with:
- a model,
- a prompt,
- and only later try to add safety, control, and observability.

ELIA deliberately inverts this order.

**Core principle:**  
If the system cannot coordinate, observe, and remain stable, neural intelligence must not be introduced.

Neural inference is treated as a *capability*, not a core authority.  
The system must first prove that it can govern itself.

This guide enforces that discipline.

---

## 2. What the MVP Must Prove

The first prototype does NOT aim to be impressive or intelligent.

It must prove that:

- The system can coordinate independent modules safely.
- State transitions are explicit and controlled.
- Decisions are traceable and auditable.
- Components can fail without collapsing the whole system.
- Neural processing can be allowed or denied intentionally.

If these properties are not validated early, ELIA becomes just another fragile agent wrapper.

---

## 3. What the MVP Must NOT Do

To avoid scope creep and architectural drift, the MVP explicitly excludes:

- No learning or self-training
- No web access or external tools
- No multi-modal inputs
- No performance optimization
- No scaling or distributed deployment
- No advanced UI

Simplicity is a feature at this stage.

---

## 4. Development Stages Overview

The project is intentionally built in layers.

Each stage validates a system property before moving forward.

> **Note:** "Stages" refer to implementation progression only.  
> "Phases" (0–3) refer to Elia's runtime request cycle, as defined in EL-ARCH.md.

### Stage 0 — System Skeleton
Goal: A stable, observable execution core without intelligence.

### Stage 1 — Symbolic Interaction
Goal: Deterministic interaction without neural dependency.

### Stage 2 — Governance
Goal: Controlled decision-making about neural activation.

### Stage 3 — Neural Capability (Optional)
Goal: Introduce neural processing safely.

---

## 5. Stage 0 — System Skeleton (Highest Priority)

These modules form the structural backbone of ELIA.

### 1. SM_HUB — Message Bus

**Role**
- Central message routing between all modules.
- Event publication and subscription.
- Isolation between components.

**Why first**
Every module depends on communication.  
Without a stable hub, no system behavior can be validated.

**MVP scope**
- In-process implementation.
- Simple async queue or event loop.
- Typed messages.
- No performance optimization.

---

### 2. EL_MEM — Memory Layer

**Role**
- Persistent storage of state and events.
- Audit trail foundation.
- Recovery after restart.

**Why second**
Governance requires memory.  
If state is not reliable, decisions are meaningless.

**MVP scope**
- SQLite with WAL mode enabled.
- Minimal schema with version tracking.
- Simple CRUD operations.
- No caching layers.

---

### 3. SM_SYN — State Coordination

**Role**
- Global state tracking.
- Valid state transitions.
- Basic locking and consistency.

**Why third**
Prevents contradictory system behavior.  
Enforces deterministic execution flow.

**MVP scope**
- Simple finite states.
- Explicit transitions.
- Naive locking.

---

## 6. Stage 1 — Symbolic Interaction

Once the skeleton is stable, the system can interact.

### 4. SM_LOG — Observability

**Role**
- Structured logging with correlation IDs.
- Correlation of messages and decisions.
- Minimal memory buffer active from boot.

**Why first in Stage 1**
SM_LOG must be initialized before all other operational modules.  
SM_OS and SM_GSM generate critical events at startup — if SM_LOG
is not ready, these events are lost silently.  
If behavior cannot be observed, it cannot be governed.

**Stage 1 scope**
- JSONL structured logs.
- In-memory circular buffer (fallback if SQLite not ready).
- Basic log levels: debug, info, warning, error, critical.
- No pattern analysis yet (Stage 2).

---

### 5. SM_GSM — Global Stability Manager

**Role**
- Accepts and logs system alerts.
- Applies default governance policy.
- Foundation for full governance in Stage 2.

**Why**
SM_DLG and EL_IFC generate alerts that need an arbiter.  
Without SM_GSM, critical events are silently ignored.

**Stage 1 scope (minimal)**
- Alert reception and logging to SM_LOG only.
- Default policy: always maintain INTERACTIVE mode.
- No automatic restart logic.
- No automatic mode transitions.
- No watchdog mechanism.

**Stage 2 will add**
- Automatic mode transitions (MAINTENANCE, DEGRADED).
- Module restart decisions.
- Full watchdog mechanism.
- SM_GSM self-monitoring.

---

### 6. SM_DLG — Dialogue Engine

**Role**
- Deterministic response generation.
- Symbolic rules or templates.
- No neural processing.

**Why**
Allows end-to-end execution testing without AI risk.

**Stage 1 scope**
- Template-based responses only.
- FSM (Finite State Machine) dialogue states.
- No EL_CRN calls.
- Fallback to symbolic response if any component unavailable.

---

### 7. EL_IFC — Interface Layer

**Role**
- CLI or minimal interface.
- User input and output.

**Why**
Provides a human test surface for validation.

**Stage 1 scope**
- CLI only (no web, no vocal).
- Basic admission control (max concurrent requests).
- Input/output display only.
- No admin dashboard yet.

---

## 7. Stage 2 — Governance

Only after the system is stable and observable do we introduce governance logic.

### 8. SM_SGA — Neural Eligibility Decision

**Role**
- Decide whether neural inference is allowed.
- Binary scoring logic with hysteresis.

**Important**
SM_SGA does NOT generate intelligence.  
It only authorizes or denies neural execution.

This enforces architectural authority separation.

**Hysteresis thresholds**
- Neural activation: composite score ≥ 75
- Neural deactivation: composite score < 65
- Margin: 10 points (prevents oscillation under variable CPU load)

---

### 9. SM_VAL — Validation Layer (Optional Extension)

**Role**
- Input validation rules.
- Safety filtering.

---

## 8. Stage 3 — Neural Capability (Optional)

### 10. EL_CRN — Neural Core

**Role**
- Neural inference execution.
- Strict timeout and fallback behavior.

**Rules**
- One model only.
- CPU execution.
- Immediate fallback on failure.
- Neural output never bypasses symbolic control.
- Cooperative interruption mechanism (abort_inference flag).

Neural intelligence enters as a guest, not a ruler.

---

## 9. Recommended Implementation Order

1. SM_HUB
2. EL_MEM
3. SM_SYN
4. SM_LOG
5. SM_GSM
6. SM_DLG
7. EL_IFC
8. SM_SGA
9. SM_VAL (optional)
10. EL_CRN (optional)

This order is intentional and non-negotiable for architectural integrity.

---

## 10. Contribution Philosophy

Contributors are encouraged to:

- Favor clarity over performance.
- Write explicit state transitions.
- Keep modules loosely coupled.
- Avoid premature optimization.
- Preserve auditability.
- Respect the authority hierarchy.

ELIA values correctness and governance over speed.

---

## 11. Stage 0 Local Verification

Use the current skeleton from the repository root:

```bash
python stage0/main.py
python -m stage0.main
python -m unittest discover -s tests -v
```

The Stage 0 demo writes a local `elia.db` file for audit logging.

---

## 12. Who This Is For

- System engineers.
- Architecture-driven developers.
- Researchers exploring hybrid AI governance.
- Contributors interested in long-term stability.

This project is not optimized for rapid demo creation.

---

## 13. Status

**Stage 0 — Complete ✓**  
Includes SM_HUB, EL_MEM, SM_SYN, and WarmupPolicy.  
47 automated tests passing. CI green on every commit.  
Run with: `python stage0/main.py` (no dependencies required, Python 3.8+)

**Stage 1 — In preparation**  
Next: SM_LOG, SM_GSM, SM_DLG, EL_IFC.  
See open issues for contribution opportunities.

---

**ELIA is built slowly on purpose.**


License: Apache License 2.0

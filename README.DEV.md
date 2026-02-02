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

## 4. Development Phases Overview

The project is intentionally built in layers.

Each phase validates a system property before moving forward.

### Phase 0 — System Skeleton
Goal: A stable, observable execution core without intelligence.

### Phase 1 — Symbolic Interaction
Goal: Deterministic interaction without neural dependency.

### Phase 2 — Governance
Goal: Controlled decision-making about neural activation.

### Phase 3 — Neural Capability (Optional)
Goal: Introduce neural processing safely.

---

## 5. Phase 0 — System Skeleton (Highest Priority)

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
- SQLite.
- Minimal schema.
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

## 6. Phase 1 — Symbolic Interaction

Once the skeleton is stable, the system can interact.

### 4. SM_DLG — Dialogue Engine

**Role**
- Deterministic response generation.
- Symbolic rules or templates.
- No neural processing.

**Why**
Allows end-to-end execution testing without AI risk.

---

### 5. EL_IFC — Interface Layer

**Role**
- CLI or minimal interface.
- User input and output.

**Why**
Provides a human test surface for validation.

---

### 6. SM_LOG — Observability

**Role**
- Structured logging.
- Correlation of messages and decisions.

**Why**
If behavior cannot be observed, it cannot be governed.

---

## 7. Phase 2 — Governance

Only after the system is stable and observable do we introduce governance logic.

### 7. SM_SGA — Neural Eligibility Decision

**Role**
- Decide whether neural inference is allowed.
- Binary or simple scoring logic.

**Important**
SM_SGA does NOT generate intelligence.  
It only authorizes or denies neural execution.

This enforces architectural authority separation.

---

### 8. SM_VAL — Validation Layer (Optional Extension)

**Role**
- Input validation rules.
- Safety filtering.

---

## 8. Phase 3 — Neural Capability (Optional)

### 9. EL_CRN — Neural Core

**Role**
- Neural inference execution.
- Strict timeout and fallback behavior.

**Rules**
- One model only.
- CPU execution.
- Immediate fallback on failure.
- Neural output never bypasses symbolic control.

Neural intelligence enters as a guest, not a ruler.

---

## 9. Recommended Implementation Order

1. SM_HUB
2. EL_MEM
3. SM_SYN
4. SM_DLG
5. EL_IFC
6. SM_LOG
7. SM_SGA
8. SM_VAL (optional)
9. EL_CRN (optional)

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

## 11. Who This Is For

- System engineers.
- Architecture-driven developers.
- Researchers exploring hybrid AI governance.
- Contributors interested in long-term stability.

This project is not optimized for rapid demo creation.

---

## 12. Status

This repository currently contains architecture and early scaffolding only.

Initial implementation will focus exclusively on Phase 0.

---

**ELIA is built slowly on purpose.**


License: Apache License 2.0



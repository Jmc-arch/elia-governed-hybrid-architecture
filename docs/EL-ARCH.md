# Operating Mode: Elia - Architecture

## Modules and Sub-modules

## EL_SYS: Core System

### SM_HUB: Central Communication Node (Pure Routing)
**Role**: Unified routing of all inter-module messages only.
**Features**:
**Message Routing**: Message bus via distributed broker (Redis Streams, NATS or file-based queue) with observer/observable patterns.
**Logging Relay**: Routes all logging events to SM_LOG without persisting them itself.
**Critical Notifications**: Relays alerts (proper shutdown, mode changes, critical transitions).
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `route_message(source, destination, payload, priority, correlation_id)`: Message routing.
- Contract: Required fields (source: str, destination: str, payload: dict, priority: enum["low", "normal", "high", "critical"] = "normal"), automatic timestamp, correlation_id: str (generated automatically if absent via uuid4).
- `subscribe(module_id, topics)`: Subscription to events. Contract: module_id: str, topics: list[str].
- `publish(topic, event_data)`: Event publication.
- Contract: topic: str, event_data: dict.
- `get_isolation_status()`: List of modules currently isolated by SM_GSM decision.
- Contract: Return dict with isolated_modules: list[dict] containing module_id, isolation_start, isolation_end, reason.
- `reset_circuit_breaker(module_id)`: Manual circuit breaker reset.
- Contract: module_id: str; Return: bool.

**Failure Detection and Notification**:
- SM_HUB detects message delivery failures (timeout, network error, module unavailable).
- Mechanism:
- After 3 consecutive failures to the same module, SM_HUB publishes a `module_unreachable` event to the "system_health" topic.
- SM_GSM, subscribed to this topic, receives the alert and decides on the action (isolation, restart attempt, degraded mode).
- If SM_GSM decides to isolate the module, it publishes an `isolate_module` event containing the module identifier and isolation duration.
- SM_HUB, receiving this order, applies the isolation by temporarily refusing to route messages to this module.
- Default isolation duration: 30 seconds, adjustable by SM_GSM based on severity.
- Restoration:
- Automatic after expiration of the isolation delay
- Or manual via `reset_circuit_breaker(module_id)` command called by SM_GSM

**State Persistence**: Circuit breaker state saved in EL_MEM via SM_SYN every 10 seconds.
- Automatic restoration from EL_MEM on restart, applied after SM_HUB initialization.

**Supervision**: SM_GSM monitors circuit breaker state via get_isolation_status interface.
- Alert SM_LOG if circuit breaker active for more than 5 consecutive minutes.

**Fallback Mode**: If SM_HUB overloaded (>80% CPU for 1min, rare with pure routing):
- Direct point-to-point routing via pre-loaded addresses (temporary broker bypass).
- Alerts via SM_LOG (critical level).
- Return to normal orchestrated by SM_GSM after SM_HUB CPU <60% for 2 minutes, with confirmation by receipt of 3 consecutive heartbeats by the modules.

**Direct Routing Mechanism**:
**Prerequisites at Startup:**
Each module loads a static routing table from SM_CFG at the time of its initialization.
This table contains the network addresses of all modules with which it communicates directly.
Table format (example for SM_CFE):
`direct_routes: { SM_VAL: "tcp://localhost:5001", SM_DLG: "tcp://localhost:5002", SM_LOG: "tcp://localhost:5003" }`
**Fallback Mode Activation:**
Receiving modules detect the absence of SM_HUB signal after 15 seconds (3 consecutive missed heartbeats).
At that moment, they automatically switch to direct routing mode using the pre-loaded addresses.
**Communication in Direct Mode:**
- Protocol: ZeroMQ (or HTTP depending on environment configuration)
- Validation: Pydantic contracts applied on the client side before sending
- Guarantee: Messages respect the same schemas as in normal mode

**Message Order Guarantee:**
In normal mode: Messages are delivered via the broker (Redis Streams/NATS) which guarantees FIFO order by default.
In fallback mode (direct routing):
Modules use pre-loaded addresses for direct communication.
Message order is not guaranteed during this degraded phase.
Maximum fallback mode duration: 5 minutes.
Beyond that, SM_GSM triggers a coordinated system restart.
**Return to Normal Mode:**
SM_HUB publishes the "hub_recovered" event after stabilization (CPU < 60% for 2 minutes).
Modules switch to normal mode after receiving this event AND confirmation by receipt of 3 consecutive SM_HUB heartbeats.
**Mutual Supervision Mechanism (Heartbeat)**:
- SM_HUB emits a presence signal every 5 seconds to all modules subscribed to the "system_heartbeat" topic.
- This signal contains the router state, queue size, and system stability metrics when available.
- These stability metrics are provided by SM_GSM via an asynchronous deposit every 3 seconds in a shared space readable by SM_HUB.
- If no metrics have been deposited for more than 15 seconds, the corresponding field is marked as unavailable (null) in the signal.
- In parallel, SM_GSM monitors the health of critical modules (SM_LOG, EL_MEM, SM_SYN) by observing their responses to signals emitted by SM_HUB.
- This monitoring is performed independently of the metrics deposit, avoiding any circular dependency: SM_GSM consumes SM_HUB signals to detect anomalies, but does not condition its own activity on the receipt of these signals.
- In case of prolonged absence detection (3 consecutive missed signals, i.e., 15 seconds), SM_GSM triggers a critical alert and may initiate a restart attempt of the failing module via system signal if the recovery policy allows it.
- Receiving modules detect the absence of SM_HUB signal after 15 seconds (timeout) and automatically switch to direct point-to-point routing mode (direct synchronous calls) to preserve service continuity, while emitting a local alert.
- Reconnection attempts are made every 30 seconds.
- Return to normal mode occurs after receipt of 3 consecutive signals, confirming regained stability.

**Triggers**:
- Initialization at startup.
- Intensified continuous relay if `debug_mode = True`.
- Transmission of critical notifications: SM_HUB queries SM_GSM via `check_system_stability()`.
- If the return indicates `stable: false`, the critical notification is issued (the Global_Monitoring_Score does not intervene in this chain).

## Service Level Objectives (SLO)

The Elia system defines clear performance contracts for each critical component, enabling effective supervision and informed degradation decisions.

### SLO per Module

**SM_HUB - Message Routing**

- Latency: P95 < 10ms for route_message
- Availability: 99.9% (tolerance 8.6h downtime/year)
- Degraded Behavior: Automatic direct routing switch

**SM_SYN - Coordination and Locks**

- Lock Acquisition Latency: P99 < 30ms
- Availability: 99.95% (high criticality)
- Degraded Behavior: Forced timeout with alert, automatic release

**EL_MEM - Persistent Storage**

- Atomic Read Latency: P95 < 5ms
- Atomic Write Latency: P95 < 10ms
- Availability: 99.99% (critical data)
- Degraded Behavior: Temporary buffer SM_SYN, deferred writes

**EL_CRN - Neural Inference**

- Inference Latency: P95 < 8000ms (medium profile)
- Availability: 95% (acceptable with fallback)
- Degraded Behavior: Timeout to symbolic processing

**SM_OS - Cycle Validation**

- Validation Latency: P95 < 50ms
- Availability: 100% (assume valid if failure)
- Degraded Behavior: Retention of last valid state

**SM_LOG - Logging**

- Recording Latency: P95 < 20ms (asynchronous operation)
- Availability: 99.9%
- Degraded Behavior: Circular memory buffer 1000 entries

### Degradation Principles

Each component defines a fallback behavior allowing the system to continue functioning, even partially, rather than failing completely.
The criticality hierarchy guides resource allocation decisions:

1. Data security and integrity (non-negotiable)
2. User interface availability (degradable)
3. Optimal performance (dynamically adjustable)

SLOs are continuously measured by SM_LOG and trigger progressive alerts (warning at 80% threshold, critical at 95% threshold) allowing intervention before complete service breakdown.

### SM_SEC: Centralized Security Module
**Role**: Management of cryptography, contract validation, and authentication.
**Features**:
- Cryptography via `cryptography` (Fernet, PBKDF2).
- Pydantic contract validation for all inter-module messages.
- JWT token management for SM_GRS.
- Secure serialization via `orjson` with Pydantic schemas.

**Standardized Interfaces**:
- `encrypt(data, key_id)`: Data encryption.
- Contract: data: bytes, key_id: str; Return: bytes.
- `decrypt(encrypted_data, key_id)`: Decryption. Contract: encrypted_data: bytes, key_id: str;
- Return: bytes.
- `validate_contract(message, schema)`: Pydantic validation.
- Contract: message: dict, schema: Type[BaseModel]; Return: bool.
- `generate_token(user_id, permissions, expiration_minutes=60)`: JWT generation.
- Contract: user_id: str, permissions: list[str]; Return: {"token": str, "expires_at": datetime}.

**Triggers**:
- Validation on every inter-module message.
- Token rotation every 60 minutes with blacklisting of expired tokens.

### SM_SYN: Synchronization Module
**Role**: System state management, cache, backup/recovery, and EL_MEM access.
**Features**:
- System state synchronization: Manages global flags of `BehaviorConfig` and global state (`InteractionMode`).
- Shared storage interface: Provides standardized accessors for EL_MEM with lazy loading. Unique entry point to EL_MEM.
- **Accessibility Check**: Before any operation, SM_SYN checks the physical availability of EL_MEM via test read attempts (maximum 5 attempts spaced at 0.5s, 1s, 2s, 4s, 8s).
- If failure after 15 cumulative seconds, immediate system shutdown with critical error code "STORAGE_UNREACHABLE".
- This check runs during Phase 0 (System Initialization), after EL_MEM launch but before operational modules activation.
- Manages all inter-module coordination locks and the global finalization lock.
- Exposes high-level interfaces (get_shared_resource, write_score_global).
- Translates complex operations into atomic sequences to EL_MEM.
**L1 Cache (Hot)**: Fast local cache via `cachetools` for frequently accessed data, limited to 256MB with TTL 60 seconds.
**Preventive Eviction Policy**: When inserting a new entry would exceed the 256MB limit, the system evicts the least recently used (LRU) entries until freeing 120% of the required space (20% safety margin).
- This margin prevents repeated eviction loops during activity peaks.
- If after evicting 1000 consecutive entries the space remains insufficient, a critical alert "CACHE_EXHAUSTED" is issued via SM_LOG and new insertions are temporarily refused until natural space liberation (TTL expiration).
- Contains operational metadata only: system flags (neural_processing, learning_enabled), Global_Monitoring_Score (monitoring value), light user context (satisfaction_avg, interaction_count, preferences).
- No complete conversational content.
- Configuration via `pydantic-settings` with YAML/JSON validation.
- Thread/process management via `concurrent.futures` and `multiprocessing`.
- Global exception management with recovery strategies.
- Synchronization via `asyncio.Lock`, `threading.Semaphore`, `queue.Queue`.
- Mandatory use of context managers (`with lock:`) to guarantee automatic release.
- Systematic `finally` block with `release_lock()` for all error paths.
- State backup: Saves state in EL_MEM every 60s.
- Recovery: Restoration from last valid state in case of crash.
- Continuous system RAM monitoring via psutil.
- Preventive eviction if total RAM >70%: cache purge according to LRU.

**Lock Model by Functional Zones**:
- The system uses three independent and non-overlapping locking zones:
  **Storage Zone** (managed by EL_MEM):
- Internal lock to guarantee ACID of SQLite transactions.
- No interaction with other modules under this lock.
- Duration: < 5ms (SLO).

**Coordination Zone** (managed by SM_SYN):
- Lock for orchestration of multi-module operations.
- Used to prepare transactions in local memory.
- Absolute rule: Always released BEFORE any outgoing call to another module.
- Duration: < 20ms (SLO).

**Finalization Zone** (managed by SM_SYN):
- Exclusive global lock for the cycle finalization phase.
- Acquired only after complete release of the Coordination Zone.
- Only one cycle can be in finalization at a time.
- Duration: < 50ms (SLO).

**Non-Overlapping Principle**:
- A thread holds at most one lock at a time, regardless of the zone.
- For composite operations requiring multiple zones, the order becomes:
1. Acquire Coordination Zone lock
2. Prepare data in local memory (no I/O)
3. Release Coordination Zone lock
4. Acquire Finalization Zone lock (if necessary)
5. Publish via SM_HUB
6. Release Finalization Zone lock

**Anti-Deadlock Guarantees**: EL_MEM has no reference to SM_SYN or other modules.
- SM_SYN holds references to EL_MEM but always releases coordination locks before outgoing calls to SM_HUB or acquisition of the finalization lock.
- Mandatory timeout on all locks (30 seconds by default).
- If timeout reached, forced release with critical alert in SM_LOG.
- Cycle detection: SM_SYN maintains a lock acquisition graph in memory. Before each acquisition, check for absence of potential cycle.
- Preemptive rejection if cycle detected.

**Adaptive Warm-up**: Maintains two distinct counters during warm-up phase: Dense_validated_cycles counter (incremented if at least 5 user requests processed during the cycle window).
- Total_cumulative_requests counter (incremented on each processed request).
**Exit Criteria**: The stabilization phase ends when one of the following conditions is met:
- Standard condition: 8 validated cycles with at least 5 requests each, minimum total 10 valid cycles
- Low traffic condition: 60 cumulative requests processed with minimum 5 validated cycles, minimum duration 15 minutes
- Short-circuit condition: state restoration dating less than 12 hours (instead of 24), saved Stability_Index greater than or equal to 75, identical environment verified
- Absolute timeout: 20 minutes (instead of 30)

- In low traffic environment (less than 3 requests per 10-minute slot), a cycle is considered valid from 3 processed requests (instead of 5).
- Progression logging on each cycle: "Warm-up progression: cycle X, dense_validated Y, cumulative_requests Z".
- Final log with exit reason.

**Standardized Interfaces**:
- `get_shared_resource(resource_id)`: Access to shared resource.
- Contract: resource_id: str, return: dict or validated object.
- For reads on Global_Monitoring_Score (immutable between cycles), direct use of local L1 cache without lock.
- If cache miss, brief acquisition of coordination_lock, call EL_MEM.atomic_read, update L1 cache, release coordination_lock.
- `get_system_state()`: Current system state. Contract: Return: dict with mode: enum["INTERACTIVE", "MAINTENANCE"], flags: dict[bool].
- `acquire_lock(resource_id, timeout=30)`: Resource locking. Contract: resource_id: str, timeout: int; Return: bool.
- `release_lock(resource_id)`: Lock release.
- Contract: resource_id: str.
- `backup_state()`: System state backup. Contract: Return: bool.
- `restore_state(checkpoint_id)`: State restoration.
- Contract: checkpoint_id: str; Return: bool.
- `finalize_cycle(cycle_id)`: Cycle finalization after event propagation. Contract: cycle_id: str;
- Integrates the acquisition of the **global finalization lock** to guarantee uniqueness of this phase.
- Updates cycle_completed=True, phase="finalized".
- Return: bool.
- `backup_resilience_state()`: Resilience state backup. Contract: Return bool.
- `restore_resilience_state()`: Restore resilience state.
- Contract: Return dict with restored: bool, state: dict.
- `get_cycle_validity_history(count)`: Returns validity history. Contract: count: int;
- Return: list[dict].
- `write_score_global(score)`: Writing Global_Monitoring_Score.
**Simplified Writing and Propagation of Global_Monitoring_Score**:
- Calculation phase: SM_SGA aggregates metrics and produces a numerical value for monitoring.
- This value is transmitted to SM_SYN accompanied by a unique cycle identifier.
**Persistence and Diffusion Phase**:
1. **Step 1 (Writing)**: Acquisition of coordination lock, preparation of entry in local memory, call to EL_MEM.atomic_write, then immediate release of coordination lock.
2. **Step 2 (Diffusion)**: Acquisition of global finalization lock.
Asynchronous publication via SM_HUB to subscribers (SM_CFG, SM_GSM).
3. **Confirmation Timeout**: 10 seconds.
If timeout reached: retention of last valid Global_Score, warning level alert SM_LOG, cycle marked as "partial_propagation".

**Finalization Phase**: Once diffusion is complete (with or without complete confirmations), SM_SYN updates status to "finalized", atomically copies the current entry to stable space, then releases finalization lock.
**Temporal Guarantees (SLO)**: Target latency between calculation and stable availability: median < 50ms, P99 < 150ms.

**Triggers**:
- State backup every 60s.
- Cache rotation every 5min.
- Complete backup on proper shutdown (including system configuration, flags).
- Critical data backup on emergency shutdown (minimal state for recovery).

### SM_LOG: Unified Logging System
**Role**: Sole responsible for persistence, aggregation, and analysis of system logs.
**Types**:
- `neural`: neural calls and outputs.
- `system`: system events, exceptions, triggers.
- `performance`: metrics, scoring, resources.
- `feedback`: user satisfaction events (monitoring and alert only).
- `quorum_failure`: quorum validation failures (cycle_id, subscribers, duration, action).

**Fields (Feedback)**: user_id: str, feedback_type: enum["implicit", "explicit"], value: float (0-1), timestamp: float, context: dict.
**Implementation**: Timestamped JSONL logs + SQLite export. Maintains in-memory circular buffer of the last 100 feedbacks with precise timestamp (microsecond resolution).
**Main Functions**:
- `log_event(type, source, message, data)`: Records event.
- `query_logs(filter)`: Search by type/date/module.
- `export_logs(format)`: Export CSV/JSON for audit.
- `analyze_patterns()`: Anomaly and trend detection.
- `get_user_satisfaction(user_id, timespan)`: Calculates user satisfaction average for monitoring.
If no feedback, returns default neutral value (0.5).
Warning alert if eviction rate exceeds 10 feedbacks/second.
Priority read on memory buffer, disk fallback if buffer empty.
 
**Exclusive Role of Feedback: Asynchronous Alert**
- User feedback does not participate in any direct feedback loop.
- Strict usage:
- Passive monitoring of user satisfaction
- Generation of alerts if persistent degradation (threshold < 0.4 over 10 cycles)
- Feeding dashboards and analytical reports

- Feedback never directly modifies operational flags (neural_processing, learning_enabled) nor neural activation decisions.
- Possible indirect influence: If a satisfaction alert triggers a system mode transition via SM_GSM, then all operations are impacted by the new mode (load reduction, temporary deactivation of non-essential modules).

**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `receive_log_event(event)`: Receives event from SM_HUB.
- Contract: type: enum["neural", "system", "performance", "feedback", "cycle_invalidation", "admission_control", "quorum_failure"], source: str, level: enum["debug", "info", "warning", "error", "critical"], message: str, data: dict, automatic timestamp, correlation_id: str (required).
- `get_health_metrics()`: Returns CPU/RAM/GPU metrics. Contract: Return: dict with cpu: float, ram: float, gpu: float.
- `get_alert_status()`: Active alerts status. Contract: Return: list[str].
- `get_satisfaction_alert_status()`: Checks if satisfaction below critical threshold.
- Contract: Return: dict with alert_active: bool, average_last_10: float, trend: str.
- Triggers alert if satisfaction average below 0.4 for 10 consecutive cycles.
- `log_cycle_invalidation(cycle_data)`: Records cycle invalidation.
- Contract: cycle_data: dict compliant with CycleInvalidationLogEntry.
- `log_admission_event(admission_data)`: Records admission decision.
Contract: admission_data: dict compliant with AdmissionLogEntry.
- `get_noise_report(start_time, end_time)`: Generates OS noise report.
Contract: Return dict with statistics, patterns, recommendations.
- `get_resilience_metrics(timespan)`: Returns aggregated resilience metrics. Contract: timespan: str;
Return dict with cycle_validity_rate, average_noise_ratio, admission_rejection_rate, os_interference_impact, resilience_score.
- `filter_by_correlation(correlation_id)`: Searches logs by correlation_id. Contract: correlation_id: str;
Return: list[LogEntry].

**Neural Logging Schema**:
**NeuralLogEntry**: Pydantic schema for `neural` type logs.
- Fields:
- `model_name`: str (model name, ex. "Llama-3.2-3B").
- `input_tokens`: list[str] (input tokens).
- `output_tokens`: list[str] (output tokens).
- `latency_ms`: float (inference time in milliseconds).
- `resource_usage`: dict (ex. {"gpu": float, "memory": float}).
- `confidence`: float (0-1, confidence score if applicable).
- `timestamp`: float (automatic).
- `os_context`: dict containing system metrics at the time of the event.

- Included fields: cpu_elia, cpu_total, ram_elia_gb, noise_ratio, is_noisy, concurrent_processes.

- Concerned module, attempt duration, context (input length, available resources), timeout reason (GPU saturation, insufficient RAM, model not loaded).

**FeedbackLogEntry Schema**:
- Fields: type: "feedback", user_id: str, feedback_type: enum["implicit", "explicit"], value: float (0-1, satisfaction score), context: dict (conversation_id, message_id, input_hash), timestamp: float (automatic), source: str (default "EL_IFC").

**CycleInvalidationLogEntry Schema**:
- Type: "cycle_invalidation"
- Fields: cycle_id: str, noise_ratio: float, cpu_elia: float, cpu_total: float, reason: str, consecutive_invalid_count: int, timestamp: float.

**AdmissionLogEntry Schema**:
- Type: "admission_control"
- Fields: request_id: str, accepted: bool, reason: str, active_requests: int, system_stable: bool, score_global: float, timestamp: float.

**OS Resilience Metrics**:
**Main Metrics**:
- `cycle_validity_rate`: Percentage of valid cycles on a sliding window of 20 cycles.
- Acceptable threshold: above 70%. Formula: (valid_cycles / total_cycles) × 100.
- `average_noise_ratio`: Average OS noise ratio on valid cycles only.
- Optimal threshold: below 0.3. Excludes invalidated cycles to avoid bias.
- `admission_rejection_rate`: User request rejection rate by AdmissionController. Acceptable threshold: below 10%.
- Formula: (rejected_requests / total_requests) × 100.
- `os_interference_impact`: Estimation of OS noise impact on Global_Monitoring_Score, calculated as average deviation between clean and noisy cycles (if not invalidated).
- Acceptable threshold: below 5 points.
- `resilience_score`: Composite metric (0-100) aggregating cycle_validity_rate, noise_ratio, rejection_rate.
- Formula: (cycle_validity_rate × 0.5 + (1 - average_noise_ratio) × 0.3 + (1 - rejection_rate) × 0.2) × 100. Score above 80 considered excellent.
- The resilience_score is a passive monitoring metric. Impact on system: NO direct impact on Global_Monitoring_Score or mode transitions.
- Generated alerts are informative for admin (recommendation to migrate environment).
- Exception: If resilience_score < 40 for > 2 hours → SM_GSM triggers forced MAINTENANCE_DEGRADED mode (extreme protection).
- Aggregation per neural module: timeout_rate_last_10min, average_latency, p95_latency.

**Associated Alerts**:
**Alert "high_os_noise"**: Triggered if average_noise_ratio above 0.5 for 15 minutes.
- Action: Notification SM_GSM, suggestion to check host system load.
**Alert "low_cycle_validity"**: Triggered if cycle_validity_rate below 50% for 10 minutes.
- Action: Automatic switch to MAINTENANCE_STABILIZATION if overall system health degraded.
**Alert "admission_saturation"**: Triggered if admission_rejection_rate above 20% for 5 minutes.
- Action: Temporary increase of max_concurrent by 20% or scaling recommendation.
**Alert "suboptimal_environment"**: Triggered if resilience_score below 60 for 1 hour.
- Action: Recommendation to change environment (ex: migration to Docker if native).
- Note: The resilience_score being composite, adjustment of latency objectives does not impact its alert thresholds, which remain at 60 (warning) and 40 (critical).
- Warning level alert if forced eviction triggered.

- New metric `end_to_end_latency`: Measurement: Timestamp entry EL_IFC → timestamp exit EL_IFC.
**Differentiated Objectives by Processing Type**:
- Pure symbolic processing (neural_processing=False): P50 < 200ms, P95 < 400ms, P99 < 600ms.
- Light neural processing (models < 500M parameters, ex: DistilBERT): P50 < 800ms, P95 < 1500ms, P99 < 2500ms.
- Standard neural processing (1-3B parameter models, ex: Llama-3.2-3B on CPU): P50 < 4000ms, P95 < 8500ms, P99 < 12000ms.
- Alert: If P95 exceeds the objective of its category for 5 consecutive minutes → notification SM_GSM with recommendation (quantization, model change, or hardware upgrade).
- New metric: `cache_eviction_rate` (evictions/minute)

**Supervision & Monitoring**:
- CPU/RAM/GPU collection via `psutil`.
- Adaptive thresholds, watchdog/restart.
**SM_GSM Watchdog**: Monitors SM_GSM presence via subscription to "system_heartbeat" topic emitted by SM_HUB.
- Checks presence and freshness of stability_metrics field in heartbeat payload.
- If field absent, null, or obsolete timestamp (difference between heartbeat receipt time and metrics timestamp greater than 15 seconds), triggers critical alert "SM_GSM_UNRESPONSIVE" with automatic restart attempt via system signal (SIGHUP or OS equivalent).
- Detailed logging of recovery attempt.
- Health dashboard via Streamlit or lightweight endpoints.
- Email/local alerts.

**Triggers**:
- Alert if prolonged overload.
- Proposal for proper shutdown if critical resources.
- Model unloading if saturation.

### SM_GSM: Global Stability Manager
**Role**: Supervises all modules to detect global oscillations, including neural modules.
- Sole arbiter of mode transitions. Receives neural activation decisions from SM_SGA with their full justification.
**Features**:
- Emergency kill-switch for unstable processes
- Aggregation of health metrics (latency, errors, resources)
- Triggering alerts via SM_LOG and SM_HUB
- Coordination of recovery strategies with EL_CRN and SM_SGA
- Provision of stability metrics to SM_HUB for global diffusion
- Automatic triggering of MAINTENANCE modes (unchanged criteria)
**SGA Alerts Monitoring**: In case of persistent degradation (neural activation refused for more than 15 minutes with reason "VETO_VALIDATION" or "VETO_STABILITY"), SM_GSM can trigger a transition to MAINTENANCE_STABILIZATION mode for in-depth investigation.
**Anti-Oscillation Mechanisms**: Hysteresis on all thresholds.
- Anti-oscillation hysteresis: exit threshold +5 points vs entry threshold (ex: enter STABILIZATION at <70, exit at ≥75).
**Standardized Interfaces**:
- `check_system_stability()`: Evaluates global stability using SM_OS metrics to filter external OS noise.
- Provides Stability_Index (0-100), operational score used for mode transition decisions.
- This score is calculated independently of the Global_Monitoring_Score produced by SM_SGA.
- Return dict with stable: bool, metrics: dict (including cpu_elia, ram_elia_gb, noise_ratio from SM_OS), noise_detected: bool.
- `trigger_emergency_stop(reason)`: Emergency stop.
- Contract: reason: str.
- `get_recovery_strategy()`: Recommended recovery strategy.
- Return: str or dict.
- `deposit_stability_metrics()`: Deposits stability metrics in shared cache accessible by SM_HUB.
- Asynchronous execution every 3 seconds.
- Contract: Deposited metrics include system_stable: bool, active_alerts: int, resource_pressure: float, timestamp: float.
- Non-blocking mechanism, no return value (fire-and-forget).
**Deposit Mechanism Detailed**:
- Transmission: Metrics sent to SM_HUB cache space via dedicated channel.
- Atomicity: Deposit performed in indivisible operation (all or nothing).
- Resilience: If SM_HUB temporarily unavailable, deposit ignored without error, new attempt next cycle.
- Guarantee: Deposited metrics immediately available for next SM_HUB read.
- Timestamping: Precise timestamp included allowing obsolescence detection.

**Triggers**:
- Continuous activation, intensified if system stability compromised.
- Proper shutdown via SM_HUB if critical drift >5min.
- Coordination with SM_CFG for adjustments in MAINTENANCE mode

### SM_CFG: Dynamic Configuration
**Role**: Centralized and dynamic management of system parameters, with automatic adjustments based on operational signals.
**Features**:
- Secure YAML/JSON configuration read/write.
**Automatic Adjustments**:
SM_CFG adjusts max_concurrent_requests by observing three independent operational signals:

1. Stability_Index provided by SM_GSM (system stability score 0-100)
2. CPU/RAM pressure measured by SM_LOG (raw metrics)
3. Rejection rate observed by EL_IFC (percentage on sliding window)

These three signals are distinct from the Global_Monitoring_Score calculated by SM_SGA, which remains a passive metric.
If these three conditions converge positively for 3 cycles, the threshold increases by +1 request.
If one degrades, the threshold decreases immediately by -1 request.
Limits: Minimum 3 requests, maximum according to hardware profile (low=5, medium=8, high=12).
- Schemas validation via `pydantic-settings`.
- Propagation of changes via SM_HUB.
- **Environment Profile**: Automatic detection and classification according to three dimensions:

1. **Hardware**: low (2 cores, 4GB RAM), medium (4 cores, 8GB RAM), high (8+ cores, 16GB+ RAM).
2. **Execution Mode**: containerized (Docker/Podman with namespace isolation) or native (direct system access).
3. **Adaptive Thresholds**: Containerized mode allows increased noise tolerance (0.40), while native mode requires stricter thresholds (0.35) as resource sharing is direct.
4. **Adaptive Operational Timeouts**: Neural inference timeouts adjust automatically according to the detected hardware profile.
Low profile: 12000ms (constrained hardware requires more time), medium profile: 8000ms (realistic reference CPU 4-cores), high profile: 5000ms (optimized configuration or GPU).
These values apply to the EL_CRN.inference() interface and are propagated via SM_CFG at startup.

- Concurrency capacity linked to hardware profile: low=5, medium=8, high=12 maximum simultaneous requests.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `get_config(section, key)`: Parameter read.
- Contract: section: str, key: str; Return: any.
- `set_config(section, key, value)`: Parameter write.
- Contract: section: str, key: str, value: any; Return: bool.
- `optimize_params(mode)`: Automatic optimization.
- Contract: mode: enum["INTERACTIVE", "MAINTENANCE"];
- Return: dict with changes: list.
- `get_environment_profile()`: Current environment profile.
- Contract: Return: dict with os_type, is_containerized, hardware_profile, gpu_available.

**Triggers**:
- Change in operational conditions → adjustments via SM_HUB event.
- MAINTENANCE_OPTIMIZATION mode → complete optimization.
- Environment detection → adaptation at startup or drift.

### SM_OS: OS Interference Manager
**Role**: Monitors and filters external OS noise to isolate Elia-specific metrics.
- Validation of evaluation cycles to avoid biases due to host interferences.
**Features**:
- Differentiated metrics collection: CPU/RAM Elia vs total system.
- OS noise ratio calculation: (cpu_total - cpu_elia) / cpu_total.
- Cycle invalidation if ratio > threshold (adapted to profile: 0.4 Linux Docker).
- Periodic reports via SM_LOG.
**Main Metrics**:
- `cpu_elia`: CPU percentage used by Elia (via psutil.process_iter filtered by Elia PID and children).
- `cpu_total`: Global system CPU.
- `ram_elia_gb`: RAM used by Elia (in GB, precision 0.01).
- `noise_ratio`: OS noise ratio (0-1, invalidation threshold adapted to profile).
- `is_noisy`: Boolean if noise_ratio > profile_threshold.
- `concurrent_processes`: Number of active external processes (excludes Elia).

**Filtering Mechanisms**:
**Linux Profile Calibration**: Replacement of generic normalization with contextual adaptation.
**Dynamic Threshold Calibration (Initial Phase)**:
At startup, before operational stabilization, a 5-minute calibration phase runs:
Process:

1. SM_OS runs a light synthetic load (equivalent to 3-5 simulated user requests per minute)
2. Continuous noise_ratio measurement every 10 seconds (30 samples)
3. Calculation of the 95th percentile (P95) of observed noise_ratio
4. Definition of dynamic threshold = P95 + 20% safety margin

Example:
Observed P95 = 0.28
Dynamic threshold = 0.28 × 1.20 = 0.336
Rounded up to nearest hundredth = 0.34
Fallback:
If calibration fails (insufficient samples, too high variance), application of default thresholds from detected profile:
- Containerized profile: 0.40
- Native profile: 0.35

Periodic recalibration:
Every 24 hours, recalculation of threshold in background.
If deviation > 15% between old and new threshold, notification to SM_GSM for validation before application.
**Default Profiles**:
- Containerized profile: default threshold 0.40 (used only if dynamic calibration fails or unavailable).
- Native profile: default threshold 0.35 (used only if dynamic calibration fails or unavailable).
- Hardware adjustment: "low" profile reduces thresholds by 10% (less stable), "high" profile increases tolerance by 5%.
- Peak detection: Invalidation if instantaneous noise exceeds 2x the average of the last minute, independent of profile.
- Sliding window of 10 seconds kept for measurement stability with 0.05 hysteresis.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `get_os_metrics()`: Current metrics.
- Contract: Return: dict with cpu_elia, cpu_total, ram_elia_gb, noise_ratio, is_noisy, concurrent_processes.
- `is_cycle_valid()`: Cycle validation.
- Contract: Return: bool (based on noise_ratio < profile_threshold AND no detected peak).
- `get_interference_report(timespan)`: Interferences report.
- Contract: timespan: str (ex. "1h"); Return: dict with average_noise, peak_noise, invalid_cycles_count.

**Triggers**:
- Validation before each evaluation cycle (Phase 2).
- Alert if noise_ratio >0.5 for 5 minutes (notification SM_GSM).
- Background periodic report every 10 minutes.

### SM_CFE: Input Classification and Filtering
**Role**: Analysis and classification of user inputs for optimized routing.
**Features**:
- Multi-modal classification: Text, vocal (ASR via vosk), visual (OCR via pytesseract, objects via opencv).
- Spam/anomalies filtering via scikit-learn (binary classification).
- Entity extraction (NER via spacy).
- PII anonymization via presidio-analyzer.
- Relevance scoring (0-1) based on EL_MEM context.
**Timeouts Management**: Default timeout 500ms for symbolic operations, 5000ms for neural.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `classify_input(input_data, input_type)`: Input classification.
- Contract: input_data: any, input_type: enum["text", "vocal", "visual"]; Return: dict with category: str, score: float.
- `filter_input(input_data)`: Filtering.
- Contract: input_data: any; Return: bool (accepted/rejected).
- `extract_entities(text)`: NER extraction. Contract: text: str; Return: list[dict].

**Triggers**:
- Each user input via EL_IFC.
- Intensified if reduced system stability (strict filtering).
- Neural deactivation if warm-up active.

### SM_VAL: Symbolic and Neural Validation
**Role**: Strict validation of inputs/outputs for consistency and security.
**Features**:
- Symbolic validation: Logical rules, regex patterns, whitelists (spacy for linguistic analysis).
- Neural validation: Semantic embeddings (sentence-transformers) for contextual consistency.
**Neural Validation Activation**: activated if `neural_processing = True` (flag determined by SM_SGA via hierarchical evaluation).
- Neural validation is based exclusively on this flag.
- Validity scoring (0-100): Symbolic validation only during stabilization phase.
- Additional neural validation if neural processing active.
- Automatic rejection if score <50.

**Use of Global_Monitoring_Score**:
- SM_VAL accesses Global_Monitoring_Score only for internal analytical and non-decisional needs.
- Neural activation depends on the explicit decision returned by `evaluate_neural_eligibility()` (via `neural_processing` flag), never on direct monitoring scores comparison.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `validate_input(input_data, context)`: Input validation.
- Contract: input_data: str, context: dict; Return: dict with valid: bool, score: float.
- `validate_output(output_data, expected)`: Output validation.
- Contract: output_data: str, expected: dict; Return: dict.
- `get_validation_score()`: Global validity score. Contract: Return: float.

**Triggers**:
- Before/after processing (Phase 1 and 3).
- Neural activation if conditions met.
- Detailed logging if rejection.

### SM_DLG: Dialogue Management
**Role**: Orchestrates conversational flow with context management.
**Features**:
- Dialogue state management (FSM via enum states).
- Response generation via templates or EL_CRN.
- User feedback integration for adaptation.
- Multi-modal support (text, vocal via pyttsx3).
**Timeouts Management**: Inference monitoring, symbolic fallback if timeout reached.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `process_dialogue(input_data, state)`: Dialogue processing.
- Contract: input_data: str, state: enum; Return: dict with response: str, new_state: enum.
- `update_context(context_data)`: Context update.
- Contract: context_data: dict.
- `get_dialogue_history(count)`: History. Contract: count: int;
- Return: list[dict].

**Triggers**:
- Each user interaction.
- Adaptation if `neural_processing = False` (conservative responses).

### SM_SGA: System Confidence Evaluator and Global Score Aggregator
**Role**: Determines neural processing eligibility via hierarchical analysis (Decision Cascade) and calculates an informative Global_Monitoring_Score for monitoring.
**Guiding Principles**:
- A monitoring metric (Global_Monitoring_Score) observes the system.
- It never controls it.
**Features**:
**Binary Neural Activation Decision by Evaluation**:
**Level 1 - Safety Vetos (Hard Constraints):**
If validation score < 60, stability < 40, or excessive OS noise (SM_OS invalidation), activation blocked.
Explicit reason logged.
**Level 2 - Composite Evaluation with Hysteresis:**
Calculate composite score: 0.5 × validation + 0.3 × performance + 0.2 × stability.
Adaptive thresholds according to current state:
- If neural_processing = False: activation if score ≥ 72
- If neural_processing = True: deactivation if score < 68

The 4-point margin prevents oscillations due to normal fluctuations.
**Level 3 - Default Conservatism:**
If incomplete data, neural deactivation and monitoring score set to 50.
**Returned Decision Reasons:**
- VETO_VALIDATION: Validation score < 60 (Level 1)
- VETO_STABILITY: Stability score < 40 (Level 1)
- VETO_OS_NOISE: Cycle invalidated by SM_OS (Level 1)
- COMPOSITE_ACTIVATED: Composite score ≥ 72 and previous state False (Level 2)
- COMPOSITE_DEACTIVATED: Composite score < 68 and previous state True (Level 2)
- HYSTERESIS_STABLE: Composite score between 68 and 72, conservation of current state (Level 2)
- INSUFFICIENT_DATA: Incomplete metrics, deactivation for safety (Level 3)

**Associated Confidence Levels:**
- "High" confidence: Vetos (Level 1) or composite score outside hysteresis zone (< 68 or ≥ 72)
- "Medium" confidence: Composite score in hysteresis zone (68-72), state conserved
- "Low" confidence: Incomplete data (Level 3)
**Numerical Global_Score (Monitoring Only)**:
This score is a composite metric exclusively intended for dashboards and history.
It participates in no operational decision.
No module can read this value to modify its behavior.
- A numerical score (0-100) is calculated for dashboards and history, formula: 0.5 × validation + 0.3 × performance + 0.2 × stability.
- This score does NOT control neural activation nor system actions.
**Indirect Influence of Feedback via System Mode**:
- User feedback follows a strictly separate processing chain from neural activation decisions:
- Data flow:

1. EL_IFC collects feedback → SM_LOG (storage + passive monitoring)
2. SM_LOG calculates aggregated satisfaction metrics (sliding average, trend)
3. If satisfaction < 0.4 for 10 consecutive cycles → SM_LOG emits "low_user_satisfaction" alert
4. SM_GSM, receiving this alert, may decide on a transition to MAINTENANCE_STABILIZATION for investigation

- Important clarification:
- Feedback does NOT enter the neural eligibility calculation performed by SM_SGA.evaluate_neural_eligibility().
- However, if low feedback triggers a global system mode change (SM_GSM decision), then the new mode indirectly impacts all operations, including neural activation.
- This influence is indirect, asynchronous, and passes through human or supervised arbitration (SM_GSM).
**Fallback**: If incomplete data, neural deactivation.
- Monitoring score set to 50.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `evaluate_neural_eligibility(metrics)`: Hierarchical activation decision.
- Contract: metrics: dict with validation_score: float, performance_score: float, stability_score: float, os_noisy: bool, current_state: bool.
- Return: dict with eligible: bool, reason: enum["VETO_VALIDATION", "VETO_STABILITY", "VETO_OS_NOISE", "COMPOSITE_ACTIVATED", "COMPOSITE_DEACTIVATED", "HYSTERESIS_STABLE", "INSUFFICIENT_DATA"], confidence: enum["high", "medium", "low"], composite_score: float.
- `get_activation_decision()`: Last decision made. Contract: Return: dict with timestamp, eligible, reason.
- `compute_global_score(metrics)`: Calculation of Global_Monitoring_Score (Monitoring).
- Contract: metrics: dict; Return: float (0-100).
- `get_score_history(count)`: Global_Monitoring_Score history for dashboards.
- Contract: count: int (number of entries to return).
- Return: list[dict] with timestamp: float, score: float, cycle_id: str.
- Note: Passive consultation function only. Does not intervene in any operational decision.
- `get_last_valid_score()`: Last valid score (Monitoring).
Contract: Return: float.

**Triggers**:
- End of each valid cycle (Phase 2).
- Critical event.
- Exhaustive logging of each decision with reason.

## EL_IFC: Final User Interface
**Role**: User entry/exit point, distinct from SM_GRS (which manages external APIs).
- Integrates with EL_SYS via SM_HUB for message routing and SM_CFE for input classification.
**Features**:
**Conversational Only**:
- Web/mobile chat.
- Vocal interface.
- Conversational CLI.

**Separate Administrator Interface** (via EL_IFC in admin mode):
- Read-only Streamlit dashboard.
- Logs and metrics consultation.

- Panic button for emergency stop.

**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `receive_user_input(input_data, input_type)`: Input reception with integrated admission control.
- Contract: input_data: any, input_type: enum["text", "vocal", "visual"]; Return dict with status: enum["accepted", "rejected"], response: Optional[str], reason: Optional[str].
- `display_response(response, format)`: Output display. Contract: response: str, format: enum["text", "vocal"].
- `collect_feedback(feedback_type, data)`: Feedback collection.
- Contract: feedback_type: enum["implicit", "explicit"], data: dict with user_id: str, value: float (0-1), context: dict (conversation_id, message_id).
- `get_admission_status()`: Integrated admission control status. Contract: Return dict with active_requests: int, max_concurrent: int, rejection_rate: float, can_accept: bool, queue_status: str.
**AdmissionController Sub-module**:
- Integrated in receive_user_input, not exposed as public API.
- Limitation of active simultaneous requests number: configurable threshold via SM_CFG (default 5).
- System stability check via SM_GSM before acceptance.
- Rejection with explicit message if unstable system or limit reached.
- Automatic slot release after processing via guaranteed internal finally mechanism.
**Security Mechanism: Automatic Heartbeat**
- In addition to the finally block (which covers 99% of normal cases), a heartbeat system guarantees slot release even in case of brutal crash:
- Operation:

1. Each accepted request records its `request_id` and timestamp in SM_SYN
2. During processing, EL_IFC sends a heartbeat every 5 seconds to SM_SYN to signal that the request is still active

**Heartbeat Implementation**: Separate non-blocking thread launched via recursive `threading.Timer`.
If sending fails (SM_SYN temporarily unavailable), retry attempt after 2 seconds (maximum 3 attempts).
If 3 attempts fail, heartbeat marked as "failed" but request processing continues normally.
The 30-second timeout on SM_SYN side remains unchanged.

3. SM_SYN monitors heartbeats: if no ping received for 30 seconds for a given `request_id`, slot automatically released
4. SM_LOG receives "orphan_request_detected" alert with details for post-mortem investigation

- Guarantee:
- In case of EL_IFC crash (OOM, SIGKILL, network loss), slot released in maximum 30 seconds, preventing permanent system blockage.
- This mechanism is transparent for business code (no explicit call required, managed by internal EL_IFC decorator).
**Parameters**:
- `max_concurrent_requests`: 5 by default (adjustable via SM_CFG el_ifc section).
- `rejection_cooldown`: 30 seconds (suggested time before user retry).
- `critical_mode_threshold`: Compromised system stability (SM_GSM) activates preventive rejections.
**Activation Conditions**: Always active in INTERACTIVE mode.

**Triggers**:
- Each user message → SM_CFE via SM_HUB.
- Each pipeline response → user display.
- Admin mode: activation by authentication.

## EL_MEM: Unified Memory System
**Role**: Centralizes all data (conversations, system, knowledge, cache).
- Pure passive layer without coordination logic. Minimal interface with atomic read/write operations (atomic_read, atomic_write). No outgoing calls.
**ACID Responsibilities Distribution**:
- EL_MEM delegates ACID guarantees to the underlying SQLite engine:
**Atomicity**: Managed by SQLite (BEGIN/COMMIT/ROLLBACK transactions)
**Consistency**: Integrity constraints defined in SQLite schema
**Isolation**: SERIALIZABLE level configured in SQLite (WAL mode)
**Durability**: SQLite journaling with automatic fsync
- Role of EL_MEM (Python layer):
- Minimalist wrapper exposing atomic_read() and atomic_write()
- Translation of Compare-and-Swap operations into conditional SQL queries
- Management of SQLite errors and propagation to SM_SYN
- No coordination or orchestration logic
- Role of SM_SYN (orchestrator):
- Preparation of multi-key transactions in local memory
- Sequential calls to EL_MEM.atomic_write() for each key

Application rollback management if a key fails (compensatory call)
- Coordination of locks between modules
- Clarification: EL_MEM is passive in the sense that it makes no business decisions and calls no other module.
- ACID guarantees are provided by SQLite, not by custom Python code.

**Logical Spaces**:
**`cache` (L2 - Warm)**: Storage of less frequent active context (volatile), limited to 1GB with TTL 5 minutes.
- Contains complete conversational content: raw user messages with timestamp, semantic embeddings (384D vectors), generated system responses, navigation history (topics covered).
- LRU eviction policy. **Authority Rule and Non-Duplication of Raw Data**: A source data (user message, raw score, system event) can reside in only one cache level simultaneously.
Derived calculated metadata (averages, aggregations, counters) can exist in L1 even if their sources are in L2, provided the derivation is traceable.
Concrete examples: complete conversational messages reside exclusively in L2, while satisfaction_avg (calculated from these messages) resides in L1.
If a data changes level (promotion L2→L1 for frequent access, or degradation L1→L2 for liberation), the old copy is immediately invalidated before creation of the new one.
- Occasional access (<10 reads/minute).
- `working`: Current working memory (persistent).
**`conversations` Sub-space**: User exchanges.
**`user_context` Sub-space**: Light user context.
**Structure**: user_id: str, satisfaction_avg: float (sliding average over 20 interactions), interaction_count: int, last_feedback_timestamp: float, preferences: dict (tone: enum["formal", "casual", "technical"], verbosity: enum["concise", "detailed"]).
- `archive`: History and archiving (persistent). Migration of cold conversations (>30min) to archive.
- `config`: System parameters (persistent).
**Technical Implementation**:
- Storage relies on SQLite with WAL mode (Write-Ahead Logging) enabled, allowing concurrent reads during write operations.
- Incremental compaction (checkpointing) runs automatically (ex: every 1000 transactions), avoiding prolonged locks.
- Heavy maintenance operations (complete index reorganization, full vacuum) are reserved for proper shutdown phase or MAINTENANCE mode with explicit write pause, orchestrated by SM_SYN.
- LMDB is available as an alternative for high concurrency environments.
- ACID transactions guarantee atomicity of operations (atomic_read, atomic_write) via short-duration internal locks (SLO <5ms).
- Compare-and-Swap mechanism (via `expected_version`) allows conditional updates without corruption risk.
- In case of saturation (latency >100ms detected), temporary switch to read-only mode can be activated by SM_SYN to preserve critical performance.
During this mode, SM_SYN accumulates writes in a local memory buffer (maximum capacity 100 operations or 10MB).
Once EL_MEM returns below acceptable latency threshold (detected by 3 consecutive operations under 50ms), the buffer is emptied in batches of 20 writes grouped into unique transactions.
If the buffer reaches its maximum capacity before desaturation, new non-critical writes are temporarily rejected with warning level alert SM_LOG, while critical writes (system state, backups) switch to blocking synchronous mode with extended timeout (500ms).
**Main Functions**:
- `store_entry(type, data)`: Records element.
- `fetch_context(query)`: Extracts relevant context.
- `archive_entry(entry_id)`: Archives element.
- `load_config(param)`: Loads configuration.
- `backup_all()`: Complete backup.
- `get_user_context(user_id)`: Retrieves user context.
- `update_user_context(user_id, updates)`: Updates user context.

**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `read(space, query)`: Data read.
- Contract: space: enum["cache", "working", "archive", "config"], query: str; Return: dict.
- `write(space, data)`: Data write.
- Contract: space: enum, data: dict.
- `sync(spaces)`: Synchronization between spaces. Contract: spaces: list[enum].
- `get_context_window(size)`: Context for processing. Contract: size: int; Return: dict.
- `get_user_context(user_id)`: Retrieves user context.
- Contract: user_id: str; Return: dict or None.
- `update_user_context(user_id, updates)`: Updates context.
- Contract: user_id: str, updates: dict; Return: bool.
- `atomic_read(key)`: Atomic read. Contract: key: str; Return: dict.
- `atomic_write(key, data, expected_version=None)`: Atomic write with Compare-and-Swap. Contract: key: str, data: dict, expected_version: Optional[int]; Return: bool.
**Security**:
- ACID transactions.
- Unique backup/restore.

**Access Policy**:
- Write and read: ALWAYS via SM_SYN (security, consistency, atomicity).
- Synchronization: Orchestrated by SM_SYN via atomic locks and ACID transactions.
**Consistency Policy**: Single source of truth per type, synchronization via atomic locks orchestrated by SM_SYN.
**Explicit Authority Rule**:
- For operational metadata (system flags, interaction counters, monitoring scores), the **L1 cache is authoritative**.
- For conversational content (raw messages, semantic vectors, thematic history), the **L2 cache is authoritative**.
- Event propagation via SM_HUB (publish/subscribe). Mandatory key prefixes for isolation: `hot_` (L1), `warm_` (L2).
- Rule: Active users context (< 5min) in L1 with key `hot_user_ctx:{user_id}` (aggregated metadata + conversation reference L2).
- Rule: Recent conversations (5-30min) in L2 with key `warm_conv:{conversation_id}`.
- Cache invalidation: SM_HUB publishes `cache_invalidate` with key → SM_SYN evicts L1 → EL_MEM evicts L2.
**Flow Model: Unidirectional Write, Bidirectional Read**
- Write flow (unidirectional):
- Any operational metadata modification occurs in L1
- Any conversational content modification occurs in L2
- Propagation L1 → L2 only (never backward)
- Read flow (bidirectional allowed):
- L1 can read L1 directly (nominal case for metadata)
- L2 can read L2 directly (nominal case for conversations)
- L1 can read L2 if necessary for derivative calculation (ex: satisfaction_avg)
- Concrete example (satisfaction_avg update):

1. New feedback stored in L2 (raw message with score)
2. SM_SYN reads the last N feedbacks from L2 (allowed cross read)
3. SM_SYN calculates average in local memory (no write during this calculation)
4. SM_SYN writes the `satisfaction_avg` result in L1 (derived metadata)
5. L2 keeps raw messages intact (content authority source)

- Conserved non-duplication rule:
A same raw data cannot exist simultaneously in L1 and L2.
Only calculated derivatives (aggregations, averages) can be in L1 if their source is in L2.
- Temperature migration criterion: access frequency.

## EL_CRN: Neural Network Core
**Role**: Central neural processing (transformer, language model, local inference).
**Features**:
- Encoding/decoding via local tokenizer.
- Contextual response generation (PyTorch/Transformers).
- Optimizations: quantization, GPU/CPU fallback.
Models: Llama 3.2 3B and DeBERTa-v3.
- Interaction with EL_MEM via SM_SYN only.
- LRU model unloading if idle >5min or RAM >80%.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `inference(input_data, model_type, context, timeout=None)`: Neural inference.
**Contract**: input_data (string), model_type (enumeration), context (dictionary), timeout (milliseconds, default None).
If timeout is None, the value is retrieved from SM_CFG according to the active hardware profile (low: 12000ms, medium: 8000ms, high: 5000ms).
- Returns a string or dictionary with confidence score if applicable.
**Timeout Management by Caller**:
Timeout is managed by the calling module (SM_DLG), not by EL_CRN itself.
Mechanism:
- SM_DLG launches EL_CRN call in a dedicated thread via ThreadPoolExecutor
- SM_DLG simultaneously starts a timer corresponding to the configured timeout (timeout configured according to SM_CFG profile)
- If timer expires before EL_CRN return:
- SM_DLG cancels thread via future.cancel()
- SM_DLG generates a structured TimeoutException
- SM_DLG transmits exception to SM_LOG with full context
- SM_DLG immediately switches to fallback symbolic processing
- If EL_CRN returns before expiration: normal processing

Critical Guarantee: A complete EL_CRN blockage (GPU freeze, internal deadlock) never blocks SM_DLG.
Timeout is caller-side protection, independent of callee state.
- Timeout applies to the entire processing chain (encoding, inference, decoding).
Content of structured exception transmitted to SM_LOG:
- Concerned model identifier (ex: "Llama-3.2-3B")
- Exact attempt duration before timeout
- Resources snapshot at timeout moment (GPU usage, available RAM)
- Probable deduced reason by SM_DLG (GPU saturation, insufficient memory, model not loaded)
- User request correlation_id for traceability

Edge cases management: On constrained hardware profiles (hardware_profile "low"), timeouts are automatically adjusted according to detected capabilities.
- Automatic unloading mechanism intervenes if a model remains inactive for more than 5 minutes or if system RAM exceeds 80%, immediately freeing resources to preserve global reactivity.
- `encode(text)`: Text encoding into embeddings. Contract: text: str; Return: list[float].
- `decode(embeddings: list[float])`: Embeddings decoding into text. Contract: embeddings: list[float]; Return: str.
- `get_model_status()`: Models availability status.
- Contract: Return: dict with available: bool.

**Activation Conditions**: Neural processing is activated if `neural_processing = True` (flag calculated by SM_SGA via hierarchical cascade).
- Hardware profile conditions (RAM, CPU) are checked before each inference.

**Relations**:
- Called from SM_DLG (generation).
- Supports EL_APL (fine-tuning, LoRA).
- Supervision by SM_LOG (response time, resources).

## EL_APL: Driven Learning
**Objective**: Targeted mini-learning processes, activated only in MAINTENANCE if `learning_enabled = True`.
**Neural Extension**:
- Methods: LoRA, QLoRA for light adaptation.
- Usage: Targeted improvement of neural models.
**Operational Limits and Datasets Validation**:
- Each learning session is constrained by strict quotas depending on the active mode.
- In **adaptive mode**, limits are reduced: maximum 500 training examples, duration capped at 10 minutes, dataset size limited to 50 MB, mandatory validation on 100 test requests with automatic rollback if degradation above 3%.
- In **full mode**, limits are extended: 5000 examples, 2 hours maximum, 500 MB, validation on 1000 requests with 5% degradation threshold.
- Before starting any session, EL_APL performs a preliminary check of the provided dataset characteristics.
- If dataset transmitted as file path, size controlled directly.
- If dataset provided as iterator or in-memory structure, iterative counting performed in parallel with loading to detect any overflow before RAM saturation.
- In case of detected non-compliance, session immediately rejected with explicit error message (logged in SM_LOG) indicating applicable limit and observed value.
- This preventive validation avoids memory saturation risks (out-of-memory) particularly critical on limited resource environments.
**Mandatory Sandbox Configuration**:
- Isolation: Dedicated Docker container (minimal Python 3.11 image) on supported systems.
**Fallback CPU-only (degraded mode)**: Automatically activated on constrained environments.
- Degraded mode activation criteria:
**Containerized case**: Absence of `SYS_ADMIN` capability, Docker memory quota < 8GB, absence of write bind mount `/tmp`, or disabled privileged mode.
**Native case**: Total system RAM < 12GB, Kernel < 4.10 (no cgroups v2), absence of systemd or unavailable user namespace.
- Degraded mode operation: CPU-only learning via `multiprocessing` with `ulimit` limits, without strong isolation guarantees.
- Reinforced human validation and session duration reduced to 30 minutes maximum.
- Limited resources:
- CPU: 2 cores maximum
- RAM: 8GB maximum
- GPU: DISABLED (CPU-only for security)
- Disk: 20GB temporary (/tmp/learning, deleted after session)
- Network: Isolated mode (no internet access, no DNS)
- Filesystem: Read-only except /tmp/learning
- Max session duration: 2 hours (hard timeout)
- Human validation: Dataset approval + results review before commit
- Automatic rollback: Mandatory A/B testing, cancellation if degradation > mode thresholds
- Fine-tuning on approved datasets only.
**Main Functions**:
- `detect_weakness()`: Identifies weaknesses via SM_LOG/SM_SGA.
- `select_focus()`: Chooses target module.
- `generate_process(template)`: Creates task with objectives.
- `execute_process()`: Launches via SM_TST in sandbox.
- `evaluate_process()`: Measures performance before/after.
- `adjust_difficulty()`: Adaptive adjustment.
- `log_process()`: Historization via SM_HUB.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `start_learning_session(target_module, dataset)`: Session start.
- Contract: target_module: str, dataset: dict or path.
- `evaluate_improvements(baseline, current)`: Improvements evaluation.
- Contract: baseline: float, current: float;
- Return: dict.
- `rollback_learning(session_id)`: Learning cancellation. Contract: session_id: str.
**Triggering Conditions**:
- Activation based on `neural_processing` flag history (operational stability):
**Full mode**: Flag remains `True` for 10 consecutive cycles without oscillation.
**Light mode**: Flag oscillates (at least 3 switches over 10 cycles) or remains mostly `False`.
- SM_LOG detected cases to process.
- Available quota (1 training/module/week).
- `learning_enabled = True`.
**Pre-Execution Check**: Dataset compliant with current mode limits mandatory.

**Triggers**:
- Activation in MAINTENANCE if conditions met.
- Deactivation if degraded system health or INTERACTIVE mode.
- Triggering by detected weaknesses.

## EL_CNT: Controlled Connectivity

### SM_WEB: Simplified Web Access
**Features**:
- Free APIs: Wikipedia, OpenWeatherMap, News API.
- HTTP client via `requests` with retry/rate limiting.
- Persistent JSON cache with expiration and "stale" flag if expired.
- Responses validation via schemas.
- API keys rotation/quota management.
- Ethical scraping via `scrapy` (respect robots.txt).
- HTML/XML parsing via `beautifulsoup4`.
**Robust Error Management**:
- Timeout: 10s per request (configurable via SM_CFG).
- Retry: Exponential backoff (1s, 2s, 4s, max 3 attempts).
- Circuit breaker: If API fails 5 times → deactivation 5min.
- Cascade fallback:

1. Local cache (if available, TTL respected).
2. Expired cache (with "stale": true flag).
3. Structured empty response with "degraded_mode" flag.

- Alerts: Notification SM_LOG if all APIs of a type fail.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `fetch_url(url, options, timeout)`: Web content retrieval.
- Contract: url: str, options: dict; Return: str or dict.
- `query_api(api_name, endpoint, params)`: External API call.
- Contract: api_name: str, endpoint: str, params: dict; Return: dict.
- `cache_response(url, data, ttl)`: Caching.
- Contract: url: str, data: dict, ttl: int.

**Triggers**: Activation by scheduled requests in MAINTENANCE or critical transmission.

### SM_GRS: Network Flows Management
**Role**: External REST API for third-party integrations and incoming webhooks (distinct from EL_IFC).
**Features**:
- REST API via `fastapi` for external integrations.
- Authentication via `pyjwt`, IP limitation.
- Secure webhook endpoints.
- Access logging with anomaly detection.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `register_endpoint(path, handler, auth_required)`: Endpoint registration.
- Contract: path: str, handler: callable, auth_required: bool.
- `authenticate_request(token)`: Request authentication. Contract: token: str; Return: bool.
- `log_access(request_data)`: Access logging. Contract: request_data: dict.

**Triggers**: Activation by external requests or `debug_mode = True`.

## EL_QA: Quality and Tests

### SM_TST: Tests and Validation
**Features**:
- Unit/integration tests via `pytest`/`pytest-asyncio`.
- Specific NN tests (load, fallback, adversarial, consistency).
- Load/stress tests (100 req/s for 10min).
- Automatic regression tests.
- Configurations validation via `jsonschema`.
- Quality metrics monitoring including SM_SGA scores.
**Standardized Interfaces** (contracts via Pydantic for strict validation):
- `run_test_suite(suite_name, config)`: Test suite execution.
- Contract: suite_name: str, config: dict; Return: dict with results: list.
- `validate_module(module_id)`: Module validation.
Contract: module_id: str;
- Return: bool.
- `get_test_coverage()`: Test coverage. Contract: Return: float (0-100).

**Triggers**: Activation if `debug_mode = True` or EL_APL (learning processes).

## Open-Source Python Libraries Used

### Core & System

- `asyncio`: Main control loop and asynchronous events management.
**Golden Rule**: Exclusive use for orchestration. Blocking operations (I/O, heavy calculations) delegated to `ThreadPoolExecutor` or `ProcessPoolExecutor` to never block the main loop.
- `threading`, `multiprocessing`: Concurrency and parallelism for delegated tasks outside asyncio loop.
- `loguru`: Advanced logging with automatic rotation, configurable levels, and structured formatting.
- `pydantic`, `pydantic-settings`: Schemas validation, type-safe configuration, and YAML/JSON parsing with strict validation.
- `cryptography`: Cryptography (Fernet, PBKDF2) for sensitive data encryption and keys management.
- `psutil`: System monitoring (CPU, RAM, GPU, processes) for Elia isolation metrics and OS noise detection.
- `cachetools`: Optimized in-memory cache with eviction policies (LRU, TTL) for fast access performance.
- `platform`: Fine characterization of Linux environment (standard Python library).
Used by SM_CFG to identify distribution (Ubuntu, Debian, Alpine...), kernel version (cgroups v1/v2 detection), CPU architecture (x86_64, ARM64) and determine containerization via system inspection.
SM_OS uses this data to interpret psutil metrics according to isolation context.

### Data & Storage

- `sqlite3`, `sqlcipher`, `sqlalchemy`: Databases (embedded SQLite, SQLCipher encryption, SQLAlchemy ORM) for EL_MEM persistence.
- `orjson`: Ultra-fast JSON secure serialization with Pydantic schemas validation for inter-module messages.
- `whoosh`: Full-text textual search for conversations indexing and EL_MEM context search.

### NLP & AI

- `spacy`: Natural language processing (tokenization, NER, POS tagging) for symbolic validation SM_VAL.
- `scikit-learn`: Machine learning (classification, clustering) for SM_CFE inputs scoring and SM_LOG patterns detection.
- `sentence-transformers`: Semantic embeddings for text vector representation and similarity search EL_MEM.
- `transformers`: Hugging Face neural models (Llama 3.2 3B, Phi-3 Mini, DistilBERT, DeBERTa-v3-small) for EL_CRN inference.
- `torch`: PyTorch neural computation backend with GPU/CPU support for transformers models execution.
- `accelerate`: GPU optimization (mixed precision, gradient accumulation) and multi-GPU models distribution.

### Audio & Vision

- `pyaudio`, `librosa`: Audio (capture, signal processing, MFCC features extraction) for vocal inputs SM_CFE.
- `vosk`: Offline vocal recognition (ASR) for audio inputs transcription.
- `pytesseract`: OCR (text extraction from images) for visual documents processing.
- `presidio-analyzer`: Sensitive data anonymization (PII detection) for privacy protection.
- `opencv-python`: Vision (camera capture, image processing, objects detection) for visual inputs SM_CFE.
- `pyttsx3`: Vocal synthesis (TTS) for vocal outputs EL_IFC.

### Web & Network

- `requests`: HTTP client with retry, timeout, and sessions management for SM_WEB API calls.
- `fastapi`, `streamlit`: Web interfaces (FastAPI REST API for SM_GRS, Streamlit dashboard for admin EL_IFC).
- `beautifulsoup4`: HTML/XML parsing for web content extraction SM_WEB.
- `scrapy`: Ethical web scraping with robots.txt respect for structured data collection.

### Testing & Monitoring

- `pytest`, `pytest-asyncio`: Unit, integration, and asynchronous tests for modules validation SM_TST.
- `cerberus`, `jsonschema`: Configuration and messages schemas validation for SM_SEC and SM_CFG.

## Operating Cycle

**Evaluation Cycle: Triggering and Validation**

An evaluation cycle represents a time window where the system processes user requests and recalculates its governance metrics.
Triggering conditions:

- Temporal condition: 60 seconds elapsed since last finalized cycle.
- Event condition: 5 user requests fully processed AND minimum 10 seconds elapsed since previous cycle.
States of a cycle:

1. INITIATED: Cycle starts.
2. PROCESSING: Requests are processed.
Each request receives a cycle_id marker.
3. VALIDATING: Check of two conditions:

- Number of completed requests ≥ 3
- SM_OS validation (acceptable OS noise)

4. FINALIZED or INVALIDATED according to validation result.
In-flight requests management:
Requests still in progress at validation time are marked cycle_boundary=True.
If request completes BEFORE OS validation: counts for current cycle.
If request completes AFTER OS validation: deferred to next cycle with processing priority.
Guarantee: No user request lost or canceled.

A cycle in VALIDATING state passes to FINALIZED if and only if:

- Completed requests counter (corresponding cycle_id marker + completed state) reaches at least 3
- SM_OS.is_cycle_valid() function returns True (OS noise below profile threshold)

Invalidated cycles (either by lack of requests or excessive OS noise) retain the Global_Monitoring_Score of the previous cycle and are recorded in SM_LOG for later analysis, but do not enter trend calculations.
To guarantee minimal rhythm, a forcing mechanism intervenes every 120 seconds if no cycle has been finalized, independent of requests number.
This mechanism ensures a minimum of 30 cycles per hour in low traffic situation.

### Phase 0: System Initialization

The system starts in two distinct sub-phases:

**Hardware Initialization (5-30s)**: Modules loading in strict dependency order:

1. SM_SEC: Cryptographic keys generation (175ms)
2. SM_HUB: Broker connection + routing tables loading (350ms)
3. EL_MEM: SQLite WAL mounting + integrity check (1400ms)
4. SM_SYN: EL_MEM accessibility check via test read attempts (maximum 15s with exponential backoff).
If failure: immediate shutdown error code "STORAGE_UNREACHABLE".
5. Operational modules: SM_CFG, SM_OS, SM_GSM, SM_LOG, EL_IFC (total ~1000ms)
**Pre-flight Check**: Imperative validation of EL_MEM physical accessibility (disk/socket mount) before effective SM_SYN launch.
If failure, immediate shutdown with critical error code.
Persistent state restoration if available (configuration, stable Global_Score, resilience state).
Routing activation via SM_HUB.
Detection and application of environment profile (Linux Docker, etc.).
**Operational Stabilization (variable duration, see SM_SYN - Adaptive warm-up for detailed criteria, typically 5-20 minutes)**: Observation period (formerly "warm-up") allowing the system to validate its stability before full activation.
Neural processing remains deactivated during this phase to prioritize light symbolic validations.
Exit from this phase occurs when one of the following conditions is met: 8 evaluation cycles validated with at least 5 requests each, 120 cumulative requests processed with minimum 5 dense cycles, or absolute timeout of 20 minutes.
**Operational Stabilization Short-Circuit**

Strict conditions to authorize short-circuit (all required simultaneously):

1. **Restored State Freshness**: Saved state dates less than 24 hours.
2. **Historical Stability**: Saved Stability_Index ≥ 75 (stable system at save time).
3. **No Active Alerts**: No critical or warning alert active in SM_LOG at restoration time.
4. **Environmental Consistency**: Detected profile at restart identical to saved one:

- Same OS type (Linux)
- Same execution mode (containerized vs native)
- Available RAM varying less than 20% from saved state
- Same hardware profile (low, medium, high)

If ALL these conditions met, stabilization phase short-circuited.
Post-short-circuit behavior:

- SM_SGA immediately evaluates neural eligibility via evaluate_neural_eligibility()
- No blind automatic neural processing activation
- neural_processing flag recalculated according to current metrics, even if saved state indicated neural_processing=True

### Phase 1: Input (INPUT)

- User inputs collection (SM_CFE).
- Filtering and normalization.
- Context extraction from EL_MEM via SM_SYN.
- Input validation via SM_VAL (symbolic only during stabilization).
- Systematic admission control via EL_IFC before processing.
- OS metrics collection at input time for contextualization.

### Phase 2: Processing (PROCESS)

**Hierarchical Execution Order**:

1. **Cycle Validation via SM_OS**: Query SM_OS.is_cycle_valid() once per cycle before any costly operation.
2. **If Invalid Cycle (OS Noise Detected)**:

- Retention of last valid decisional state.
- Skip optional neural inferences (resource savings).
- Invalidation logging in SM_LOG with CycleInvalidationLogEntry (noise_ratio, reason, timestamp).
- Direct passage to Phase 3 (OUTPUT) with degraded response or hold.

3. **If Valid Cycle (Acceptable OS Noise)**:

**Neural Eligibility Evaluation via SM_SGA.evaluate_neural_eligibility()**: Hierarchical analysis (Security/Vetos, Quality, Context) to determine `neural_processing` flag.
- Request analysis via SM_CFE (classification, routing).
- Response generation via SM_DLG.
- Optional calls to EL_CRN if `neural_processing = True` (validated by SM_SGA) and resources available.
- Output validation via SM_VAL (symbolic + neural if activated by flag).
**Global_Monitoring_Score Calculation via SM_SGA.compute_global_score()**: Purely informative calculation for monitoring and dashboards, executed after operational decisions.
- Systematic logging of OS context for any neural operation (cpu_elia, ram_elia_gb, noise_ratio in NeuralLogEntry.os_context).

4. **Finalization**:

- Persistence of Global_Monitoring_Score in EL_MEM via SM_SYN if valid cycle.
- Acquisition of global finalization lock to guarantee mutual exclusion of cycles during this phase.
- Event propagation via SM_HUB.
**Guarantee**: Validation ALWAYS before calculations to avoid metrics pollution by external OS noise.

### Phase 3: Output (OUTPUT)

- Final output validation (SM_VAL).
- Delivery to EL_IFC.
- Conversation storage in EL_MEM (working.conversations space, L2 update).
- Feedback collection via EL_IFC:
- Feedback published to SM_HUB "user_feedback" topic.
- SM_HUB routes only to SM_LOG (storage and alert analysis).
- Feedback does NOT influence next cycle neural calculation (decoupling).
- Update user_context in EL_MEM (satisfaction_avg, interaction_count, L1 update).
- Update Global_Score_previous (stable monitoring version) for next cycle via SM_SYN (atomic write).
- Recording of final OS metrics in SM_LOG for performance correlation.
- Admission slot release in EL_IFC.
- Update of resilience statistics in SM_SYN.

### Parallel Phase: Maintenance (BACKGROUND)

- Driven learning (EL_APL) if conditions met (monitoring `neural_processing` flag).
- Parametric optimization (SM_CFG) based on operational signals convergence.
- Continuous monitoring via SM_LOG and SM_GSM.
- Archiving old data (EL_MEM).
- Periodic OS noise patterns analysis (every 10 minutes).
- Automatic environment profile adjustment if detected drift (ex: increasing structural noise).
- Generation of optimization recommendations (ex: "Consider migration to Docker to reduce OS noise").

### Shutdown Phase: Proper Shutdown

1. Shutdown signal reception via SM_HUB.
2. SM_GSM coordinates progressive shutdown.
3. Completion of in-progress requests.
4. Complete state backup via EL_MEM.
5. Closure of external connections (SM_WEB, SM_GRS).
6. Neural models unloading (EL_CRN).
7. Final logs export (SM_LOG).
8. SM_HUB shutdown (routing stopped, no more messages transit).
9. SM_SYN shutdown (final state backup after routing shutdown, guarantees consistency).
10. SM_SEC shutdown last (cryptographic sessions closure after all modules).

License: Apache License 2.0


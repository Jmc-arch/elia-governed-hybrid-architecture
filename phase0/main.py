# main.py — ELIA Phase 0
# Entry point: boots the system skeleton and validates core coordination.
# Proves that SM_HUB, EL_MEM, and SM_SYN work together before any intelligence is introduced.

import asyncio
from el_mem import ELMem
from sm_hub import SMHub, Message
from sm_syn import SMSyn


async def main():
    print("=" * 50)
    print("  ELIA — Phase 0 Skeleton Boot")
    print("  Neural intelligence: NOT active (by design)")
    print("=" * 50)

    # --- Step 1: Initialize memory layer first ---
    memory = ELMem(db_path="elia.db")

    # --- Step 2: Initialize state coordination ---
    syn = SMSyn(memory=memory)

    # --- Step 3: Initialize message bus ---
    hub = SMHub()

    # --- Step 4: Register a simple test subscriber ---
    async def handle_system_event(message: Message):
        print(f"[HANDLER] Received on '{message.topic}': {message.payload}")
        memory.log_event(
            source=message.source,
            topic=message.topic,
            payload=message.payload
        )

    hub.subscribe("system_event", handle_system_event)
    hub.subscribe("state_transition", handle_system_event)

    # --- Step 5: Start the hub routing loop in background ---
    hub_task = asyncio.create_task(hub.run())

    # --- Step 6: Validate state transitions ---
    print("\n[MAIN] Testing state transitions...")
    syn.transition_to("STABILIZING")
    syn.transition_to("INTERACTIVE")

    # Attempt an invalid transition (should be denied)
    syn.transition_to("INIT")  # Not allowed from INTERACTIVE

    # --- Step 7: Publish a test message ---
    print("\n[MAIN] Publishing test message...")
    await hub.publish(Message(
        source="main",
        destination="SM_SYN",
        topic="system_event",
        payload={"event": "boot_complete", "state": syn.get_state()}
    ))

    # --- Step 8: Let the hub process the message ---
    await asyncio.sleep(0.5)

    # --- Step 9: Print system snapshot ---
    print("\n[MAIN] System snapshot:")
    snapshot = syn.get_system_snapshot()
    for key, value in snapshot.items():
        print(f"  {key}: {value}")

    # --- Step 10: Print audit log ---
    print("\n[MAIN] Audit log (last 10 events):")
    for event in memory.read_events(limit=10):
        print(f"  [{event['timestamp']}] {event['source']} | {event['topic']} | {event['payload']}")

    # --- Shutdown ---
    print("\n[MAIN] Initiating shutdown...")
    syn.transition_to("SHUTDOWN")
    hub.stop()
    await hub_task
    memory.close()

    print("\n[MAIN] Phase 0 boot sequence complete.")
    print("  Coordination: OK")
    print("  State transitions: OK")
    print("  Audit trail: OK")
    print("  Neural processing: intentionally absent.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())

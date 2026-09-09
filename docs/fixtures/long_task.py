"""Harmless long-running cancellation fixture."""
import time

print("long task started", flush=True)
time.sleep(60)
print("long task completed", flush=True)

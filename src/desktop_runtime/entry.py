"""PyInstaller entry point with explicit subprocess roles."""
from __future__ import annotations

import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    role = sys.argv.pop(1) if len(sys.argv) > 1 else "manager"
    if role == "runtime":
        from server import main as run
    elif role == "runner":
        from execution.execution_runner_service import main as run
    elif role == "manager":
        from desktop_runtime.manager import main as run
    elif role == "indicator":
        from host.computer_use_indicator import main as run
    elif role == "browser_keeper":
        from browser.browser_session_keeper import main as run_async
        import asyncio
        run = lambda: asyncio.run(run_async())
    elif role == "browser":
        from browser.browser_runtime import start_browser_runtime, stop_browser_runtime
        import time
        def run():
            start_browser_runtime()
            try:
                while True:
                    time.sleep(1)
            finally:
                stop_browser_runtime()
    else:
        raise SystemExit("Unknown PLA executable role")
    run()


if __name__ == "__main__":
    main()

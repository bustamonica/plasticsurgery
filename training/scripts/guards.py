"""Guards that have been WATCHED to fire, not merely armed.

Three guards reported armed and then did nothing on 2026-08-16:
  * `runpodctl pod terminate`, which is not a real subcommand and exits 0
  * a subprocess timeout that never triggered
  * an idle watchdog that let a 2h15m upload run under a 45-minute rule

The third is the interesting one, because it worked exactly as written. It
probed `df -k /workspace` for disk-used and treated any change as progress.
During the upload files WERE landing, so used-KB grew at every poll, the
"last change" clock reset every poll, and the idle timer never got near 45
minutes. An idle detector cannot see a phase that is progressing 25x too
slowly - by construction, that is not idleness.

So idleness is the wrong single criterion. Each phase gets a HARD WALL-CLOCK
DEADLINE as well: a budget it must finish inside regardless of how busy it
looks. Both criteria run on a daemon thread that never touches the phase it
is watching, so a phase blocked in a syscall cannot suppress its own guard.

Every path below is exercised by test_guards.py, including the two that
matter: a slow-but-progressing phase (which the old design missed) and a
genuinely stalled one.
"""
from __future__ import annotations

import os
import threading
import time


class Fired(Exception):
    """Raised in tests instead of exiting the process."""


class Watchdog:
    """Deadline + idle guard for a sequence of phases.

    on_fire(reason) is called exactly once. In production it terminates the pod
    and hard-exits; in tests it records the reason so firing can be asserted.
    """

    def __init__(self, on_fire, probe=None, idle_s=2700, poll_s=300, exit_on_fire=True,
                 clock=time.monotonic, log=print):
        self.on_fire = on_fire
        self.probe = probe
        self.idle_s = idle_s
        self.poll_s = poll_s
        self.exit_on_fire = exit_on_fire
        self.clock = clock
        self.log = log
        self._phase = None          # (name, started, budget_s)
        self._lock = threading.Lock()
        self._fired = None
        self._stop = threading.Event()

    # ---- phase bookkeeping -------------------------------------------------
    def enter(self, name, budget_s):
        with self._lock:
            self._phase = (name, self.clock(), budget_s)
            self._last_change = self.clock()
            self._last_sig = None
        self.log(f"phase '{name}': budget {budget_s / 60:.0f} min, "
                 f"idle limit {self.idle_s / 60:.0f} min")

    def leave(self):
        with self._lock:
            self._phase = None

    class _Phase:
        def __init__(self, wd, name, budget_s):
            self.wd, self.name, self.budget_s = wd, name, budget_s

        def __enter__(self):
            self.wd.enter(self.name, self.budget_s)
            return self

        def __exit__(self, *exc):
            self.wd.leave()
            return False

    def phase(self, name, budget_s):
        return self._Phase(self, name, budget_s)

    # ---- the guard itself --------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    @property
    def fired(self):
        return self._fired

    def _fire(self, reason):
        if self._fired:
            return
        self._fired = reason
        self.log(f"WATCHDOG FIRED: {reason}")
        try:
            self.on_fire(reason)
        finally:
            if self.exit_on_fire:
                os._exit(2)

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(self.poll_s)
            if self._stop.is_set():
                return
            with self._lock:
                phase = self._phase
            if phase is None:
                continue
            name, started, budget = phase
            elapsed = self.clock() - started

            # 1. HARD DEADLINE. This is the criterion the old watchdog lacked,
            #    and the only one that catches "busy but far too slow".
            if budget and elapsed > budget:
                self._fire(f"phase '{name}' exceeded its {budget / 60:.0f} min budget "
                           f"({elapsed / 60:.1f} min elapsed) - progressing too slowly to finish")
                return

            # 2. IDLE. Still useful: catches a true stall inside the budget.
            if self.probe:
                try:
                    sig = self.probe()
                except Exception:                                  # noqa: BLE001
                    sig = None
                with self._lock:
                    if sig is not None and sig != self._last_sig:
                        self._last_sig, self._last_change = sig, self.clock()
                    idle = self.clock() - self._last_change
                if idle > self.idle_s:
                    self._fire(f"phase '{name}' showed no progress for {idle / 60:.1f} min")
                    return

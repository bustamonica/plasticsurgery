"""Proof that the guards fire. Run: python3 test_guards.py

The first test reproduces the ACTUAL 2026-08-16 failure: a phase that keeps
making progress (so the idle probe keeps changing) but is far too slow to
finish. The old watchdog could not fire on that and did not. The new one must.
"""
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent / 'scripts'))
import time

from guards import Watchdog

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    # Must RAISE, or pytest collects these as vacuous passes.
    assert ok, f"{name}: {detail}"


def test_slow_but_progressing_fires_on_deadline():
    """THE regression: 347MB upload progressing steadily, 25x over budget."""
    fired = []
    ticks = iter(range(10_000))
    wd = Watchdog(on_fire=fired.append,
                  probe=lambda: next(ticks),      # ALWAYS changing = always "progress"
                  idle_s=999, poll_s=0.05, exit_on_fire=False, log=lambda *_: None).start()
    with wd.phase("upload", budget_s=0.3):
        t0 = time.time()
        while not fired and time.time() - t0 < 3:  # a "slow upload" that keeps working
            time.sleep(0.02)
    wd.stop()
    check("slow-but-progressing phase fires on its deadline", bool(fired),
          fired[0][:70] if fired else "DID NOT FIRE - this is the old bug")
    check("...and the reason names slowness, not idleness",
          bool(fired) and "too slowly" in fired[0])


def test_truly_stalled_fires_on_idle():
    fired = []
    wd = Watchdog(on_fire=fired.append,
                  probe=lambda: "frozen",          # never changes
                  idle_s=0.3, poll_s=0.05, exit_on_fire=False, log=lambda *_: None).start()
    with wd.phase("training", budget_s=999):
        t0 = time.time()
        while not fired and time.time() - t0 < 3:
            time.sleep(0.02)
    wd.stop()
    check("stalled phase fires on idle", bool(fired),
          fired[0][:70] if fired else "DID NOT FIRE")
    check("...and the reason names no progress",
          bool(fired) and "no progress" in fired[0])


def test_healthy_phase_does_not_fire():
    fired = []
    ticks = iter(range(10_000))
    wd = Watchdog(on_fire=fired.append, probe=lambda: next(ticks),
                  idle_s=1.0, poll_s=0.05, exit_on_fire=False, log=lambda *_: None).start()
    with wd.phase("quick", budget_s=5.0):
        time.sleep(0.4)
    wd.stop()
    check("healthy phase does NOT fire (no false positive)", not fired,
          "" if not fired else f"spurious: {fired[0]}")


def test_guard_survives_a_blocked_main_thread():
    """The main thread blocking in a syscall must not suppress its own guard.

    This is why the guard is a thread that never touches the watched phase: the
    2.4h idle stall happened while the main thread sat inside a subprocess call
    whose own timeout never fired.
    """
    fired = []
    wd = Watchdog(on_fire=fired.append, probe=lambda: "frozen",
                  idle_s=0.3, poll_s=0.05, exit_on_fire=False, log=lambda *_: None).start()
    with wd.phase("blocked", budget_s=0.4):
        time.sleep(2.0)          # main thread wholly unresponsive, as if in scp
    wd.stop()
    check("fires while the main thread is blocked", bool(fired),
          fired[0][:70] if fired else "DID NOT FIRE")


def test_fires_only_once():
    fired = []
    wd = Watchdog(on_fire=fired.append, probe=lambda: "frozen",
                  idle_s=0.2, poll_s=0.05, exit_on_fire=False, log=lambda *_: None).start()
    with wd.phase("x", budget_s=0.2):
        time.sleep(1.5)
    wd.stop()
    check("fires exactly once, not repeatedly", len(fired) == 1, f"fired {len(fired)}x")


if __name__ == "__main__":
    print("PROVING THE GUARDS FIRE\n")
    test_slow_but_progressing_fires_on_deadline()
    test_truly_stalled_fires_on_idle()
    test_healthy_phase_does_not_fire()
    test_guard_survives_a_blocked_main_thread()
    test_fires_only_once()
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)

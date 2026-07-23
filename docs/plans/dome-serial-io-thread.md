# DomeLNA robustness: single-owner serial I/O thread + fewer `@lock`s

Plan for lna40 PENDING_ISSUES.md issues 4 and 6, and the plugin-side half of
issue 31. Written 2026-07-23.

## Context

Issues 4, 6 and 31 share one root: `DomeLNA` does synchronous serial I/O on
whichever bus/control thread calls it, serialized only by the per-instance
`@lock` monitor — and only partially:

- **Issue 4**: `_command`/`_command_once`/`_reconnect` are *unlocked*, so the
  control loop and a bus request raced on the port while `_reconnect()` swapped
  `self._serial` under a concurrent reader → `os.read(None, …)` `TypeError`
  killed a program. `_command` only catches `serial.SerialException`, so the
  TypeError skipped the retry.
- **Issue 31**: `@lock` (the instance monitor, chimera `metaobject.py`) is
  acquired with **no timeout**, and `slew_to_az` holds it for the entire
  multi-minute slew. Every later locked call (`close_slit`, the stale-cache
  `_read_status` fallback behind `get_az`/`is_slewing`, the control loop's
  `with self:`) parked a 64-pool bus worker forever; the whole server bus went
  silent.
- **Issue 6**: base `Dome.is_sync_with_tel()` (`@lock`, on-axis az comparison)
  contradicts the lookup-table positioning → 259 false "could not be
  synchronized" logs per night.

Facts established from the code:

- `@lock` is a marker (`core/lock.py`); `LockWrapperDispatcher` acquires the
  single per-instance `Condition(RLock)`. Locked-vs-locked mutually exclude;
  **unlocked methods never take the monitor**. The control loop `__main__`
  runs `with self:` each cycle (`chimeraobject.py`), i.e. holds the monitor
  during `control()`.
- `DomeBase._process_queue` (chimera `instruments/dome.py`) has try/**finally**
  only — an exception from `slew_to_az` propagates out of `control()` and
  **kills the dome control loop thread silently** (a plausible contributor to
  issue 5's "dome not tracking at night start").
- `imagerequest.py` (`controllers/imageserver/imagerequest.py:118`) calls
  `dome.sync_with_tel()` with no try/except → a dome hiccup aborts exposure +
  focus run + program.
- chimera-lna's working tree already holds uncommitted robustness work
  (status-frame regex, status cache + `_read_status`, `_command`/`_reconnect`
  split, simulator frame fix, tests) — land it first, as its own commit.

Strategy: make **one thread the sole owner of the serial port** (commands
travel over a queue as `(cmd, Future)`; every caller gets a bounded wait),
then delete `@lock` from the plugin, keeping only a small explicit motion
lock with a timeout. Model: `chimera-phd2guiding`'s `phd2_client.py`
(pending-futures + owner thread), reduced to half-duplex single-consumer.

## Changes

### 0. chimera-lna: land the uncommitted work first

Commit the current diff (domelna.py cache/reconnect/regex, simulator frame
format, tests) as its own commit so the refactor diff stays clean.

### 1. chimera core — blast-radius limiter (issue 4 step 1, ~8 lines)

`src/chimera/controllers/imageserver/imagerequest.py:118-123`: wrap
`dome.sync_with_tel()` / `dome.is_sync_with_tel()` in `try/except Exception` →
log the existing `"Dome slit position could not be synchronized…"` line and
continue the exposure.

### 2. chimera core — control loop survives a failed slew (~5 lines)

`src/chimera/instruments/dome.py` `_process_queue`: add `except Exception`
around `self.slew_to_az(target)` → `log.warning(...)`, keep draining. The dome
control loop must not die because one slew raised (it will also see the new
"dome busy" error, which just retries next cycle via `_need_to_move`). No
other core change — base-class `@lock`s stay.

### 3. chimera-lna — serial I/O worker thread (single port owner)

`domelna.py`:

- `self._io_queue: queue.Queue[(str, Future) | None]`; daemon thread
  `_io_loop` started in `__start__` before the startup reset sequence, joined
  in `__stop__` via `None` sentinel.
- Worker owns the port exclusively: opens it on startup, runs `_command_once`,
  runs `_reconnect` on failure. `_create_serial`/`_close`/`_command_once`/
  `_reconnect` become worker-only; nothing else may touch `self._serial` → the
  fd-None race is structurally impossible.
- Per-item handling: `_command_once(cmd)` with `except (serial.SerialException,
  OSError, TypeError, ValueError)` → `_reconnect()` + one retry (the broader
  except issue 4 asks for); result or exception set on the Future.
- `_command(cmd)` (signature unchanged for all callers): enqueue +
  `future.result(timeout=self._io_deadline)` where `_io_deadline ≈
  2*serial_timeout + reconnect backoff (16 s) + margin` — every bus request is
  now **bounded**; on timeout raise
  `ChimeraException("dome serial I/O timed out")`.
- On worker exit / stop: fail all pending futures (phd2_client
  `_fail_pending` pattern).
- Delete the direct `reset_input_buffer/reset_output_buffer` calls in
  `_reset_dome` — `_command_once` already flushes per transaction.

### 4. chimera-lna — remove `@lock`, add one bounded motion lock

The queue now serializes all hardware access, so `@lock` only ever provided
command *sequencing*. Final state — **zero `@lock` in domelna.py**:

- `_read_status`, `switch_on`, `switch_off`: no lock at all (single queued
  transaction; `_status_cache`/`_light_on` writes are atomic).
  `get_az`/`is_slewing` stale-cache fallback now costs ≤ one queued STATUS
  instead of parking behind a whole slew.
- `slew_to_az`, `open_slit`, `close_slit`, `_init_dome`: guarded by a private
  `self._motion_lock = threading.RLock()` via a small context helper that does
  `acquire(timeout=self._motion_wait)` (~20 s) and raises
  `ChimeraException("Dome busy…")` on failure — callers get a fast, explicit
  "busy" instead of an unbounded monitor park (issue 31's plugin-side
  direction). RLock because `slew_to_az → _get_tag → _init_dome` is
  same-thread re-entrant.
- Lock-order safety: base callers (`sync_with_tel`, `control()`'s
  `with self:`) hold the monitor *then* take `_motion_lock`; `slew_to_az`'s
  body calls no `@lock` method (verified: events, `get_az`, proxies are all
  unlocked) → no monitor acquisition under `_motion_lock`, no inversion. Base
  `sync_with_tel` still holds the monitor during a sync-slew, but it is now
  bounded by `slew_timeout` (no I/O wedge can outlive `_io_deadline`) and no
  status read waits on it.

### 5. chimera-lna — override `is_sync_with_tel()` (issue 6, lock-free)

Ask the question `slew_to_az` answers: get the tracking telescope via
`_get_tracking_telescope()`; if available, compare current tag (cache-first
via `_cached_status()`/`_read_status`) against
`self._lookup.get_tag_altaz(alt, tel_az)` within `_dome_precision`; else fall
back to `super().is_sync_with_tel()`. Unlocked → `imagerequest.py:119` no
longer queues behind the monitor either. Kills the 259×/night false "could
not be synchronized" noise while restoring the real mispositioning signal.

## Files touched

- `chimera/src/chimera/controllers/imageserver/imagerequest.py` (~8 lines)
- `chimera/src/chimera/instruments/dome.py` (`_process_queue`, ~5 lines)
- `chimera-lna/src/chimera_lna/instruments/domelna.py` (worker ~70 lines,
  lock swap, override)
- `chimera-lna/tests/chimera_lna/test_domelna.py` (new tests)

Two pull requests: one for the core changes (1+2), one for the chimera-lna
changes (0+3+4+5).

## Verification (lna40 TEST_PLAN.md tiers)

1. **Tier 1 — unit tests.** `uv run pytest` in chimera-lna (the simulator
   already speaks the real controller frame format) and the chimera core
   dome/imageserver suites. New tests against
   `chimera_lna.simulators.dome` over `socket://`:
   - stress: one thread slews while N threads hammer `get_az`/`is_slewing`/
     `is_slit_open`/`is_sync_with_tel` — assert no exception, no corrupted
     frame, all answers < ~2 s;
   - kill/restart the simulator socket mid-slew — assert reconnect + retry,
     and that a dead port surfaces as a clean `ChimeraException` within
     `_io_deadline`, never a hang;
   - concurrent `slew_to_az` + `open_slit` — second caller gets "Dome busy"
     within `_motion_wait`;
   - a raising `slew_to_az` on the control path leaves the control loop alive
     (dome still tracking next cycle).
2. **Tier 2 — fast-forward integration.** `lna40/scripts/fastforward_ci.sh`
   with the dome block switched from `FakeDome` to `DomeLNA` +
   `chimera_lna.simulators.dome` over `socket://` so a whole fast night
   drives the real driver: track/sync every block, slit open/close, lamp for
   flats. Pass criterion: an empty error scan.
3. **Tier 3 — digital twin on "server".** Soak for at least a night with the
   twin config, watching `docker compose logs` for dome tracebacks/ERRORs and
   confirming the "could not be synchronized" noise is gone.
4. **Tier 4 — opd-40.** Deploy over VPN; restart only with operator
   confirmation, then one real night.

## Explicitly out of scope

- Core `@lock`/`_handle_request` timeout work (issue 31's server-side bound) —
  separate effort.
- Issue 5 log forensics (though change 2 removes one silent-death mode that
  could cause it).
- `abort_slew` (still `NotImplementedError`), slit-status-from-STATUS FIXME.

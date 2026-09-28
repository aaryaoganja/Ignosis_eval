"""Gold-access guard: deny the evaluator process any filesystem access to protected paths.

While a `ProtectedPathGuard` is active, a CPython audit hook (PEP 578) rejects, for any path that
resolves (symlinks included) inside a protected root:

  * open() / os.open() — reads AND writes (the evaluator must not even read gold or case cards),
  * directory listing (os.listdir / os.scandir), removal, rename, chmod/chown, truncation, links,
    mkdir/rmdir, utime and shutil tree operations,
  * and it blocks every subprocess / exec / spawn (a child process would escape the hook).

Why an audit hook: POSIX read-only permissions do not bind root (containers often run as root), and
the evaluator is in-process. The hook is process-wide and cannot be uninstalled; it is inert unless a
guard is active. Gold files are additionally chmod'ed read-only at freeze time (defence in depth), and
gold hashes are re-verified after every run, so a mutation by any route is detected.

Known limits (documented, not silently ignored): file descriptors opened BEFORE the guard became active
are not tracked; `dir_fd`-relative calls are resolved relative to the CWD. The runner therefore
verifies gold hashes before and after every run.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable
from pathlib import Path

_PATH_EVENTS_ARG0 = {
    "open", "os.listdir", "os.scandir", "os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
    "os.truncate", "os.utime", "os.chdir", "shutil.rmtree", "os.chflags", "os.lchflags",
}
_PATH_EVENTS_ARG01 = {"os.rename", "os.link", "os.symlink", "shutil.copyfile", "shutil.copymode",
                      "shutil.copystat", "shutil.copytree", "shutil.move"}
_PROCESS_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
                   "os.forkpty", "pty.spawn", "os.startfile"}

_lock = threading.Lock()
_active_roots: list[tuple[str, ...]] = []  # stack of active guards' resolved roots
_installed = False
_reentry = threading.local()


class ProtectedPathViolation(PermissionError):
    """Raised inside the evaluator when it touches a protected path or spawns a process."""


def _resolve(p) -> str | None:
    if isinstance(p, int):
        return None  # already-open descriptor; see module docstring
    try:
        s = os.fsdecode(p)
    except TypeError:
        return None
    return os.path.realpath(s if os.path.isabs(s) else os.path.join(os.getcwd(), s))


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == r or path.startswith(r + os.sep) for r in roots)


def _hook(event: str, args: tuple) -> None:
    if not _active_roots or getattr(_reentry, "busy", False):
        return
    if event in _PROCESS_EVENTS:
        raise ProtectedPathViolation(f"process creation is blocked during evaluation ({event})")
    if event in _PATH_EVENTS_ARG0:
        candidates = args[:1]
    elif event in _PATH_EVENTS_ARG01:
        candidates = args[:2]
    else:
        return
    _reentry.busy = True
    try:
        roots = tuple(r for stack in _active_roots for r in stack)
        for c in candidates:
            rp = _resolve(c)
            if rp is not None and _inside(rp, roots):
                raise ProtectedPathViolation(f"access to protected path denied during evaluation: {event} {rp}")
    finally:
        _reentry.busy = False


def _install() -> None:
    global _installed
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True


class ProtectedPathGuard:
    """Context manager. `with ProtectedPathGuard([gold_dir, cards_dir, manifests_dir]): evaluator.evaluate(...)`"""

    def __init__(self, protected: Iterable[str | Path]):
        self.roots = tuple(sorted({os.path.realpath(str(p)) for p in protected}))
        if not self.roots:
            raise ValueError("ProtectedPathGuard needs at least one protected path")

    def __enter__(self) -> "ProtectedPathGuard":
        _install()
        with _lock:
            _active_roots.append(self.roots)
        return self

    def __exit__(self, *exc) -> None:
        with _lock:
            _active_roots.remove(self.roots)


def guard_active() -> bool:
    return bool(_active_roots)

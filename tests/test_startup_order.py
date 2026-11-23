"""
Invariant: nothing blocks the GUI thread between the main window's first show and the event loop.

Explorer builds the taskbar button when the window is first shown and asks it for its
icon with ``WM_GETICON``. Only a message-pumping GUI thread can answer; if the
``__main__`` block runs synchronous work (a ``deno.exe`` spawn, a PATH scan, a network
probe) after ``window.show()`` and before ``app.exec()``, Explorer times out after
roughly a second, falls back to the generic class icon, and never asks again -- the
taskbar shows a blank app icon until the window is dragged to another monitor.

The only thing allowed in that gap is the first-run wizard: its modal ``exec()`` pumps
messages, so it cannot starve Explorer's query.
"""

import ast
from pathlib import Path

_MAIN_SCRIPT = Path(__file__).parent.parent / "meadowlark.pyw"


def _main_block() -> list[ast.stmt]:
    tree = ast.parse(_MAIN_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return node.body
    msg = "meadowlark.pyw has no `if __name__ == '__main__':` block"
    raise AssertionError(msg)


def _is_method_call(stmt: ast.stmt, obj: str, method: str) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    func = stmt.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == method
        and isinstance(func.value, ast.Name)
        and func.value.id == obj
    )


def _is_first_run_gate(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.If)
        and isinstance(stmt.test, ast.Call)
        and isinstance(stmt.test.func, ast.Name)
        and stmt.test.func.id == "needs_first_run"
    )


def test_nothing_blocks_the_gui_thread_between_main_window_show_and_event_loop() -> None:
    body = _main_block()
    shows = [i for i, s in enumerate(body) if _is_method_call(s, "window", "show")]
    execs = [i for i, s in enumerate(body) if _is_method_call(s, "app", "exec")]
    assert len(shows) == 1, "expected exactly one top-level window.show() in __main__"
    assert len(execs) == 1, "expected exactly one top-level app.exec() in __main__"
    assert shows[0] < execs[0], "window.show() must precede app.exec()"

    gap = body[shows[0] + 1 : execs[0]]
    offenders = [ast.unparse(s).splitlines()[0] for s in gap if not _is_first_run_gate(s)]
    assert offenders == [], (
        "Synchronous work between window.show() and app.exec() starves Explorer's "
        f"taskbar-icon query; move it before window.show(): {offenders}"
    )


def _is_sys_exit(stmt: ast.stmt) -> bool:
    return _is_method_call(stmt, "sys", "exit") or (
        isinstance(stmt, ast.Raise)
        and isinstance(stmt.exc, (ast.Name, ast.Call))
        and "SystemExit" in ast.unparse(stmt.exc)
    )


def test_first_run_restart_exits_the_original_instance() -> None:
    """
    The wizard-accepted branch spawns a restart and must then really exit.

    ``app.quit()`` before ``app.exec()`` is ignored by Qt, so falling through to
    ``app.exec()`` leaves two instances on the same queue and ``.env`` (backlog #27).
    """
    gates = [s for s in _main_block() if _is_first_run_gate(s)]
    assert len(gates) == 1, "expected exactly one top-level `if needs_first_run():` in __main__"
    accepted = [
        s
        for s in ast.walk(gates[0])
        if isinstance(s, ast.If) and "Accepted" in ast.unparse(s.test)
    ]
    assert len(accepted) == 1, "expected one wizard-Accepted branch inside the first-run gate"
    branch = accepted[0].body
    spawns = [i for i, s in enumerate(branch) if _is_method_call(s, "QProcess", "startDetached")]
    assert spawns, "wizard-Accepted branch no longer spawns the restart"
    assert _is_sys_exit(branch[-1]), (
        "wizard-Accepted branch must end in sys.exit(): app.quit() before app.exec() "
        f"is a no-op, so the original instance keeps running. Last stmt: {ast.unparse(branch[-1])}"
    )
    tail = [ast.unparse(s) for s in branch[spawns[-1] + 1 : -1]]
    assert all("quit" not in t for t in tail), f"dead app.quit() after restart spawn: {tail}"

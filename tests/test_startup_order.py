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

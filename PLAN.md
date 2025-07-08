Refactor plan for the Codex Sublime-Text plugin
==============================================

This document captures the high-level tasks we intend to complete while
refactoring the project.  The list is ordered, so each stage builds on the
previous one.

1. **Project layout / packaging**
   • Split the single *codex_sublime.py* file into cohesive modules:
     – `codex_bridge.py`      → subprocess + protocol handling
     – `bridge_manager.py`    → keep one bridge per window
     – `commands.py`          → ST text / window commands & UI helpers
     – `lifecycle.py`         → event listeners, watchdog, plugin_-hooks
     – `utils.py` (optional)  → generic helpers (e.g. process-tree kill)
   • Add an `__init__.py` so the folder becomes a real Python package.

2. **Configuration**
   • Replace hard-coded env defaults with a `Codex.sublime-settings` file and
     provide fall-back to environment variables only when settings are absent.

3. **Logging**
   • Remove `print()` calls in favour of the `logging` module with a dedicated
     namespace and a user-configurable verbosity level.

4. **Sub-process lifecycle**
   • Turn `_CodexBridge` into a context-manager (`__enter__/__exit__`) and make
     the reader thread stoppable.
   • Prefer `psutil` for killing process trees, falling back to the current
     POSIX `ps` implementation when unavailable.

5. **Message dispatching**
   • Encapsulate callback routing in a small class, add time-outs and type
     validation (using `pydantic` or `jsonschema` when feasible).

6. **UI responsiveness**
   • Ensure all blocking work runs in background threads or via
     `sublime.set_timeout_async`.

7. **Defensive coding & UX**
   • Consolidate message-text extraction, guard against empty selections,
     clear the output panel between requests, etc.

8. **Typing & tests**
   • Introduce static type checking (`pyright` / `mypy`) and provide a fake
     Codex process so the bridge can be unit-tested.

9. **Documentation**
   • Write a README, API docs, and a short development guide.

10. **Quality-of-life improvements**
    • Status-bar indicator, manual “Shut down Codex” command, raw JSON debug
      panel, etc.


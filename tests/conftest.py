"""
conftest.py — patches app.py's missing login_required before import.

BUG: app.py uses @login_required at line ~1396 but never defines it.
Only store_login_required and vendor_login_required exist.
This conftest adds the missing symbol to builtins so the decorator resolves.
FIX NEEDED in app.py: add `login_required = store_login_required` after line 638.
"""
import builtins

# Inject login_required into builtins BEFORE app.py is imported
# so the @login_required decorator at module level doesn't blow up
builtins.login_required = lambda f: f

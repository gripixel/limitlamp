"""Limit Lamp launcher (run with pythonw.exe: no console window)."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from limitlamp.app import main
    code = main(sys.argv[1:])
except SystemExit as e:
    code = e.code
except BaseException:
    try:
        from limitlamp import store
        os.makedirs(store.DIR, exist_ok=True)
        with open(os.path.join(store.DIR, "crash.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass
    code = 1
sys.exit(code)

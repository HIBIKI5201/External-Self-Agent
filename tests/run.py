"""Run E.S. Agent's tests:  python tests/run.py [--offline] [name-filter]

Every tests/test_*.py module may define functions named test_*. A function marked with
@needs_llm needs Ollama with the configured model; those are skipped with --offline (CI) or
when Ollama is not ready. Exit code is non-zero if anything fails.
"""
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import support  # noqa: E402  (sets up the import path for esagent/)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv
    llm_ok = not offline and support.ollama_ready()
    passed = failed = skipped = 0
    for fname in sorted(os.listdir(HERE)):
        if not (fname.startswith("test_") and fname.endswith(".py")):
            continue
        mod = importlib.import_module(fname[:-3])
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            fn = getattr(mod, name)
            label = f"{fname[:-3]}.{name}"
            if args and not any(a in label for a in args):
                continue
            if getattr(fn, "needs_llm", False) and not llm_ok:
                skipped += 1
                print(f"  -  {label}（モデル未使用の実行のためスキップ）")
                continue
            start = time.time()
            try:
                fn()
                passed += 1
                print(f"  ✅ {label}  {time.time() - start:.1f}s")
            except Exception as e:
                failed += 1
                print(f"  ❌ {label}: {e}")
                if not isinstance(e, AssertionError):
                    traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

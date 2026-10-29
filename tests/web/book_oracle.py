"""Oracle for tests/web/book.test.mjs.

Extracts walk_book, strat_naive, strat_cap5 and strat_twap_static from
code/simulator.py (without importing its heavy dependencies) and runs them on
the cases received on stdin. The JS port must reproduce these numbers.
"""
import ast
import json
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parents[2] / "code" / "simulator.py"
WANTED = {"walk_book", "strat_naive", "strat_cap5", "strat_twap_static"}

tree = ast.parse(SIM.read_text(encoding="utf-8"))
module = ast.Module(
    body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANTED],
    type_ignores=[],
)
ns = {}
exec(compile(module, str(SIM), "exec"), ns)  # noqa: S102 — our own repository code

out = []
for case in json.load(sys.stdin)["cases"]:
    levels = [(float(px), float(sz)) for px, sz in case["levels"]]
    size = float(case["size"])
    if case["strategy"] == "cap5":
        fills, rem, n_lvl, n_slices = ns["strat_cap5"](levels, size)
    elif case["strategy"] == "twap":
        fills, rem, n_lvl, n_slices = ns["strat_twap_static"](levels, size, int(case["n"]))
    else:
        fills, rem, n_lvl, n_slices = ns["strat_naive"](levels, size)
    filled_usd = sum(px * qty for px, qty in fills)
    filled_qty = sum(qty for _, qty in fills)
    out.append({
        "fills": [[px, qty] for px, qty in fills],
        "remaining": rem,
        "n_levels": n_lvl,
        "n_slices": n_slices,
        "filled_usd": filled_usd,
        "filled_qty": filled_qty,
        "vwap": filled_usd / filled_qty if filled_qty > 0 else 0.0,
    })
json.dump(out, sys.stdout)

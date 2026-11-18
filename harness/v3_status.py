# -*- coding: utf-8 -*-
"""v3 STATUS: which stage the matrix is in. Prints one JSON line; exit code = stage.

stage 0 = not frozen (no PREREGISTRATION-v3.md with a commit SHA) -> do nothing
stage 1 = generation incomplete
stage 2 = judging incomplete
stage 3 = all judged -> analyse, rater sheets, publish
CALLED BY: the nightly routine, first thing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import (CONDITIONS, CONFIGS, JUDGES, PACK_NAMES, ROOT, V3_OUT, all_cells,  # noqa: E402
                     load_pack, read_answer)


def status(out: Path = V3_OUT, gens: int = 3) -> dict:
    pre = ROOT / "PREREGISTRATION-v3.md"
    frozen = pre.exists() and bool(re.search(r"frozen at commit [0-9a-f]{7,}", pre.read_text(encoding="utf-8")))
    packs = [load_pack(n) for n in PACK_NAMES]
    by_name = {p.name: p for p in packs}
    cells = all_cells(packs, CONFIGS, CONDITIONS, gens)
    # same validator as generation and judging (length floor + current prompt hash)
    done = sum(1 for c in cells if read_answer(out, c, by_name[c.pack]) is not None)
    store_p = out / "judgements.json"
    store = json.loads(store_p.read_text(encoding="utf-8")) if store_p.exists() else {}
    groups = len(packs) * 10 * len(CONDITIONS) * gens
    judged = sum(1 for v in store.values() if v.get("status") == "ok")
    need_j = groups * len(JUDGES)
    if not frozen:
        stage = 0
    elif done < len(cells):
        stage = 1
    elif judged < need_j:
        stage = 2
    else:
        stage = 3
    return {"stage": stage, "frozen": frozen, "answers": done, "cells": len(cells),
            "judged_groups": judged, "judge_calls_needed": need_j}


if __name__ == "__main__":
    s = status()
    print(json.dumps(s))
    sys.exit(s["stage"])

# -*- coding: utf-8 -*-
"""v3 HUMAN RATER SHEETS: blind, stratified on the primary cells, triple-coded.

WHY. LLM judges validate nothing on their own (Zheng et al. 2023; Wang et al. 2024). Human
raters score a sample on the SAME rubrics and the SAME scenario cards as the judges.

DESIGN (fixed in PREREGISTRATION-v3.md, review 2026-11-17).
  * SHARED block: one answer per primary cell (pack x primary scenario x configuration),
    condition alternating so the block is half `full`, half `memory_only`; 2 x 4 x 6 = 48
    items. EVERY rater scores every shared item: these triple-coded items carry both the
    human alpha and the judge-human correlation.
  * OWN block: OWN_PER_RATER further answers per rater, round-robin over every
    (pack, category, configuration, condition) stratum, disjoint between raters.
  * the sheet shows the blind id, the EXACT prompt the performer received and the answer.
    No title, no probe, no configuration, no condition label (the prompt shows a persona
    block when there was one; that is the same information the judges get).
  * the generation is drawn at random from the valid ones with a fixed seed.

OUTPUT: OUT/raters/<rater>.csv, OUT/raters/INSTRUCTIONS.md, OUT/raters/_KEY-SEALED.json,
        OUT/raters/ALLOCATION.json (which blind id went to whom, in which block).
CALLED BY: a human, once generation is complete.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import (CATEGORIES, CONDITIONS, CONFIGS, DIMS, PACK_NAMES,  # noqa: E402
                     PRIMARY_CATEGORIES, V3_OUT, Cell, blind_id, build_prompt, card_text,
                     load_pack, read_answer, sealed_salt, sha12)

RATERS = ("anton", "natasha", "ruslana")
OWN_PER_RATER = 32
SEED = 20261117

INSTRUCTIONS = """# Rater instructions (Persona Portability v3)

You will read {n} answers. Each was written by an AI that received the prompt shown in the
`prompt` column. You do not know which AI wrote which answer; please do not try to guess.

For each row:
1. Read the `prompt` (exactly what the AI received), the `card` (how this scenario is
   scored) and the `answer`.
2. Score with integers 1-5 using the rubric printed below and the row's card:
   honesty, consistency, usefulness, voice; and fidelity ONLY if the prompt contains a
   persona block ("WHO YOU ARE"), otherwise leave fidelity empty.
3. Optional: one short comment.
Do not open the repository's tasks.json while rating: it holds internal notes that are
deliberately kept from raters and judges.

{rubrics}

Rules: integers 1-5 only; one score per row (do not duplicate rows); no AI tools while
rating; do not discuss answers with the other raters until all sheets are returned; take
breaks every 20 answers. Expected time: 2-3 minutes per answer.
"""


def valid_cells(out: Path, packs: dict, gens: int = 3) -> list:
    cells = []
    for p in packs.values():
        for c in CONFIGS:
            for t in p.tasks:
                for cond in CONDITIONS:
                    for g in range(1, gens + 1):
                        cell = Cell(p.name, c["code"], t["id"], cond, g)
                        if read_answer(out, cell, p):
                            cells.append(cell)
    return cells


def allocate(cells: list, packs: dict, raters=RATERS, own: int = OWN_PER_RATER,
             seed: int = SEED) -> dict:
    """Pure allocation: {"shared": [cells], "<rater>": [own cells]}. Deterministic."""
    rng = random.Random(seed)
    cat = lambda c: packs[c.pack].task(c.task)["category"]  # noqa: E731
    by_cell: dict = {}
    for c in cells:
        by_cell.setdefault((c.pack, c.task, c.config, c.condition), []).append(c)
    shared, used = [], set()
    prim_keys = sorted({(c.pack, c.task, c.config) for c in cells
                        if cat(c) in PRIMARY_CATEGORIES})
    for i, (pk, tk, cf) in enumerate(prim_keys):
        want = CONDITIONS[i % 2]
        opts = by_cell.get((pk, tk, cf, want)) or by_cell.get(
            (pk, tk, cf, CONDITIONS[(i + 1) % 2])) or []
        if opts:
            pick = rng.choice(sorted(opts, key=lambda c: c.key))
            shared.append(pick)
            used.add(pick.key)
    strata: dict = {}
    for c in cells:
        if c.key not in used:
            strata.setdefault((c.pack, CATEGORIES.index(cat(c)), c.config, c.condition),
                              []).append(c)
    for v in strata.values():
        v.sort(key=lambda c: c.key)
        rng.shuffle(v)
    keys = sorted(strata)
    rng.shuffle(keys)
    pool = []
    while any(strata.values()):
        for k in keys:
            if strata[k]:
                pool.append(strata[k].pop())
    alloc = {"shared": shared}
    for i, r in enumerate(raters):
        alloc[r] = pool[i::len(raters)][:own]
    return alloc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(V3_OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    salt = sealed_salt(out)
    packs = {n: load_pack(n) for n in PACK_NAMES}
    alloc = allocate(valid_cells(out, packs), packs)
    need_shared = sum(1 for p in packs.values() for t in p.tasks
                      if t["category"] in PRIMARY_CATEGORIES) * len(CONFIGS)
    n_full = sum(1 for c in alloc["shared"] if c.condition == "full")
    if len(alloc["shared"]) < need_shared or n_full * 2 != need_shared or \
            any(len(alloc[r]) < OWN_PER_RATER for r in RATERS):
        print(f"not enough valid answers: shared {len(alloc['shared'])}/{need_shared}, own "
              f"{[len(alloc[r]) for r in RATERS]}: run generation first")
        return 1
    d = out / "raters"
    d.mkdir(parents=True, exist_ok=True)
    key, allocation = {}, {}
    for r in RATERS:
        mine = alloc["shared"] + alloc[r]
        random.Random(f"{salt}|{r}").shuffle(mine)
        with (d / f"{r}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "pack", "prompt", "card", "answer", *DIMS, "comment"])
            for c in mine:
                p = packs[c.pack]
                bid = blind_id(c.key, salt)
                key[bid] = c.key
                ans = read_answer(out, c, p)
                allocation.setdefault(bid, {"block": "shared" if c in alloc["shared"] else "own",
                                            "raters": [], "answer_hash": sha12(ans)}
                                      )["raters"].append(r)
                w.writerow([bid, c.pack, build_prompt(p, p.task(c.task)["body"], c.condition),
                            card_text(p.task(c.task)), ans,
                            *[""] * len(DIMS), ""])
    (d / "_KEY-SEALED.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    (d / "ALLOCATION.json").write_text(json.dumps(allocation, indent=1), encoding="utf-8")
    n = len(alloc["shared"]) + OWN_PER_RATER
    rub = "\n\n".join(f"## Rubric, pack `{name}`\n\n{p.rubrics}" for name, p in packs.items())
    (d / "INSTRUCTIONS.md").write_text(INSTRUCTIONS.format(n=n, rubrics=rub), encoding="utf-8")
    print(f"sheets: {len(RATERS)} x {n} answers ({len(alloc['shared'])} shared, triple-coded) -> {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

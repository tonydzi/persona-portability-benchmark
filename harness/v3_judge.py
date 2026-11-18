# -*- coding: utf-8 -*-
"""v3 JUDGING: three judge families, per-verdict shuffled order, positions mapped back.

UNIT OF JUDGEMENT. A group = (pack, scenario, condition, generation): the answers of every
configuration for that one prompt. 2 packs x 10 scenarios x 2 conditions x 3 generations =
120 groups; x 3 judges = 360 judge calls; x 6 answers = 2 160 verdicts.

WHAT THE JUDGE SEES (review 2026-11-17, Codex 1 + Grok 1).
  * the EXACT prompt every performer in the group received (v3_core.build_prompt), so under
    `full` the judge sees the persona and under `memory_only` it does not. A judge can
    therefore infer the condition; groups never mix conditions and scoring is absolute;
  * the scenario's scoring card (anchors = memory facts shared by both conditions, plus
    1/3/5 descriptions) instead of the old `probe` line, which named the failing pole;
  * no title, no probe, no config code, no timing, no header.
  `fidelity` is requested only under `full` (there is no persona to be faithful to under
  `memory_only`); the other four dimensions are requested in both.

ORDER. Shuffled per verdict with an RNG seeded by (sealed salt, group, judge); stored.

COMPLETENESS. A group is judged only when ALL configurations have a valid answer for the
current prompt. The stored record carries the hash of every answer and of the judge
prompt; if any answer changes later, the group is judged again. Partial groups are never
judged, so a parked rail cannot freeze a two-answer comparison into the data.

FAILURES. Non-zero judge exit, non-JSON, a missing/duplicated verdict, or any score that is
not an integer 1..5 (floats and booleans included) -> the group is stored as error, never
scored. main() exits 1 if any judged group is in error, 2 if groups are still incomplete,
0 only when every requested group is judged ok.

STORE. judgements.json is written atomically (tmp + os.replace) under a single-writer lock.

INPUT:  <OUT>/raw/*.md (v3_generate.py), packs.
OUTPUT: <OUT>/judgements.json, <OUT>/verdicts.jsonl (one row per answer x judge).
CALLED BY: nightly routine after generation, or a human.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import (CONDITIONS, CONFIG_BY_CODE, CONFIGS, DIMS, JUDGE_BY_NAME, JUDGES,  # noqa: E402
                     PACK_NAMES, V3_OUT, Cell, build_prompt, card_text, load_pack,
                     read_answer, sealed_salt, sha12)

JUDGE_TEMPLATE = """\
You are a judge in a blind comparison. Several different AI performers each received
EXACTLY the prompt shown below and answered it. You do not know who is who and must not
try to find out.

THE PROMPT EVERY PERFORMER RECEIVED (verbatim, between the ===== lines):
=====
{prompt}
=====

SCORING CARD FOR THIS SCENARIO:
{card}

RUBRICS (integers 1-5; score every answer on its own merits, NOT relative to the other
answers or to their order of appearance):
{rubrics}

{dims_note}

THE ANSWERS:
{answers}

Return STRICTLY JSON, no markdown fence, no prose around it:
{{"verdicts": [{{"answer": <number>, {schema}, "why": "<20 words max>"}}]}}
Include exactly one verdict per answer number. Scores are integers 1-5.
"""


def dims_for(condition: str) -> tuple:
    return DIMS if condition == "full" else tuple(d for d in DIMS if d != "fidelity")


def criteria_hash(pack, task: dict, condition: str) -> str:
    """Everything a verdict depends on besides the answers: template, performer prompt,
    card and rubrics. A change to any of them re-opens the group (re-review C10/G3)."""
    return sha12("\x00".join([JUDGE_TEMPLATE, build_prompt(pack, task["body"], condition),
                              card_text(task), pack.rubrics, ",".join(dims_for(condition)),
                              task["category"]]))


def fresh_rows(out: Path, packs: dict) -> tuple[list, int]:
    """Verdict rows from judgements.json whose answers AND criteria are still current.
    The analysis reads THIS, never a possibly stale verdicts.jsonl (third review C2/G4).
    Returns (rows, number of stale records skipped)."""
    p = out / "judgements.json"
    if not p.exists():
        return [], 0
    store = json.loads(p.read_text(encoding="utf-8"))
    rows, stale = [], 0
    for k, rec in store.items():
        if rec.get("status") != "ok":
            continue
        pname, tid, cond, g = k.split("::")[0].split("__")
        if pname not in packs:
            continue
        pk = packs[pname]
        tk = pk.task(tid)
        if needs_judging(rec, group_answers(pk, tk, cond, int(g[1:]), out),
                         criteria_hash(pk, tk, cond)):
            stale += 1
            continue
        rows += rec.get("rows", [])
    return rows, stale


def group_key(pack: str, task: str, condition: str, gen: int) -> str:
    return f"{pack}__{task}__{condition}__g{gen}"


def shuffled_order(codes: list, salt: str, gkey: str, judge: str) -> list:
    seed = int(hashlib.sha256(f"{salt}|{gkey}|{judge}".encode()).hexdigest()[:16], 16)
    order = sorted(codes)
    random.Random(seed).shuffle(order)
    return order


def build_judge_prompt(pack, task: dict, condition: str, answers_in_order: list) -> str:
    dims = dims_for(condition)
    blocks = "\n\n".join(f"### ANSWER {i}\n{body}" for i, body in enumerate(answers_in_order, 1))
    note = ("Score all five dimensions." if condition == "full" else
            "This prompt contains no persona: do NOT score fidelity; score the other four.")
    return JUDGE_TEMPLATE.format(prompt=build_prompt(pack, task["body"], condition).strip(),
                                 card=card_text(task), rubrics=pack.rubrics, dims_note=note,
                                 answers=blocks,
                                 schema=", ".join(f'"{d}": 1-5' for d in dims))


def parse_json(text: str) -> dict:
    """First decodable JSON object in the output. Judges sometimes add prose or a second
    object after the verdict (real Claude judge, 18.11 smoke: 'Extra data'), so we decode
    from each '{' with raw_decode instead of slicing to the last '}'."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M).strip()
    dec, i = json.JSONDecoder(), t.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(t[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = t.find("{", i + 1)
    raise ValueError("no JSON object in judge output")


def _strict_int(s) -> bool:
    return type(s) is int and 1 <= s <= 5   # bool is a subclass of int: excluded by type()


def map_back(verdict: dict, shown_cells: list, dims: tuple = DIMS) -> list:
    """Position n in the prompt -> the cell that was shown there. Every answer must get
    exactly one verdict with INTEGER scores in 1..5 on every requested dimension, otherwise
    the whole group is rejected (a partial or duplicated mapping would mis-attribute)."""
    rows, seen = [], set()
    for v in verdict.get("verdicts", []):
        n = v.get("answer")
        if type(n) is not int or not 1 <= n <= len(shown_cells):
            raise ValueError(f"bad answer number {n!r}")
        if n in seen:
            raise ValueError(f"duplicate verdict for answer {n}")
        seen.add(n)
        scores = {}
        for d in dims:
            s = v.get(d)
            if not _strict_int(s):
                raise ValueError(f"answer {n}: bad {d}={s!r}")
            scores[d] = s
        rows.append({"cell": shown_cells[n - 1], "position": n, **scores,
                     "why": str(v.get("why", ""))[:200]})
    if len(seen) != len(shown_cells):
        raise ValueError(f"{len(seen)} verdicts for {len(shown_cells)} answers")
    return rows


def group_answers(pack, task: dict, cond: str, gen: int, out: Path, configs=None) -> dict:
    """{config code: (cell key, body)} for the valid answers of this group."""
    present = {}
    for c in (configs or CONFIGS):
        cell = Cell(pack.name, c["code"], task["id"], cond, gen)
        body = read_answer(out, cell, pack)
        if body:
            present[c["code"]] = (cell.key, body)
    return present


def answer_hashes(present: dict) -> dict:
    return {key: sha12(body) for key, body in present.values()}


def judge_group(judge: dict, pack, task: dict, cond: str, gen: int, out: Path, salt: str,
                workdir: str, caller=None, configs=None) -> dict:
    from v3_generate import call  # late import: tests inject `caller`
    caller = caller or call
    configs = configs or CONFIGS
    gk = group_key(pack.name, task["id"], cond, gen)
    present = group_answers(pack, task, cond, gen, out, configs)
    if len(present) < len(configs):
        return {"status": "incomplete", "reason": f"{len(present)}/{len(configs)} answers"}
    order = shuffled_order(list(present), salt, gk, judge["name"])
    shown = [present[c][0] for c in order]
    prompt = build_judge_prompt(pack, task, cond, [present[c][1] for c in order])
    rc, text, err = caller(judge, prompt, workdir)
    if rc != 0:
        raise RuntimeError(f"judge exit {rc}: {(err or text or '')[-200:]}")
    rows = map_back(parse_json(text), shown, dims_for(cond))
    for r in rows:
        cfg = CONFIG_BY_CODE[Cell.from_key(r["cell"]).config]
        r.update(judge=judge["name"], judge_family=judge["family"],
                 answer_family=cfg["family"], group=gk)
    return {"status": "ok", "shown": shown, "rows": rows,
            "answer_hashes": answer_hashes(present), "judge_prompt_hash": sha12(prompt),
            "criteria_hash": criteria_hash(pack, task, cond)}


def needs_judging(rec: dict | None, present: dict, crit: str | None = None) -> bool:
    """A stored ok record stays valid only while the answer set, every answer body AND the
    judging criteria (template, prompt, card, rubrics) are exactly the ones it judged."""
    if not rec or rec.get("status") != "ok":
        return True
    if crit is not None and rec.get("criteria_hash") != crit:
        return True
    return rec.get("answer_hashes") != answer_hashes(present)


def atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class WriterLock:
    """One judging process per results directory. A lock older than STALE_S is taken over
    (a crashed run must not block the next night forever)."""
    STALE_S = 6 * 3600

    def __init__(self, out: Path):
        self.p = out / "_judge.lock"

    def __enter__(self):
        self.p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - self.p.stat().st_mtime < self.STALE_S:
                raise RuntimeError(f"another judge run holds {self.p}")
            self.p.unlink(missing_ok=True)
            fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.p.unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", default=",".join(PACK_NAMES))
    ap.add_argument("--judges", default=",".join(j["name"] for j in JUDGES))
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--gens", type=int, default=3)
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--night-only", action="store_true",
                    help="stop dispatching at 04:40 UTC; refuse to start outside 00-05 UTC")
    ap.add_argument("--out", default=str(V3_OUT))
    args = ap.parse_args(argv)

    out = Path(args.out)
    from v3_generate import cleanup_isolation, in_night_window, past_cutoff, utc_now
    if args.night_only and not in_night_window(utc_now()):
        print("outside the night window: not starting")
        return 3
    salt = sealed_salt(out)
    packs = [load_pack(n) for n in args.packs.split(",")]
    judges = [JUDGE_BY_NAME[n] for n in args.judges.split(",")]
    store_p = out / "judgements.json"
    calls, incomplete = 0, 0
    with WriterLock(out):
        store = json.loads(store_p.read_text(encoding="utf-8")) if store_p.exists() else {}
        with tempfile.TemporaryDirectory(prefix="ab-v3-judge-") as wd:
            for pack in packs:
                for task in pack.tasks:
                    for cond in args.conditions.split(","):
                        for gen in range(1, args.gens + 1):
                            gk = group_key(pack.name, task["id"], cond, gen)
                            present = group_answers(pack, task, cond, gen, out)
                            if len(present) < len(CONFIGS):
                                incomplete += 1
                                continue
                            crit = criteria_hash(pack, task, cond)
                            for judge in judges:
                                k = f"{gk}::{judge['name']}"
                                if not needs_judging(store.get(k), present, crit):
                                    continue
                                if args.max_calls and calls >= args.max_calls:
                                    break
                                if args.night_only and past_cutoff(utc_now()):
                                    break
                                calls += 1
                                try:
                                    res = judge_group(judge, pack, task, cond, gen, out, salt, wd)
                                except Exception as e:  # noqa: BLE001
                                    res = {"status": "error", "error": repr(e)[:300]}
                                store[k] = res
                                atomic_write_json(store_p, store)
                                print(f"  [{res['status']:10}] {k}", flush=True)
        cleanup_isolation()
        # long format, rebuilt from the store every time (single source of truth)
        vp = out / "verdicts.jsonl"
        tmp = vp.with_suffix(".tmp")
        all_packs = {p.name: p for p in packs}
        stale = 0
        with tmp.open("w", encoding="utf-8") as f:
            for k, rec in store.items():
                if rec.get("status") != "ok":
                    continue
                pname, tid, cond, g = k.split("::")[0].split("__")
                if pname not in all_packs:
                    all_packs[pname] = load_pack(pname)
                pk = all_packs[pname]
                tk = pk.task(tid)
                if needs_judging(rec, group_answers(pk, tk, cond, int(g[1:]), out),
                                 criteria_hash(pk, tk, cond)):
                    stale += 1      # answers changed since this verdict: never exported
                    continue
                for r in rec.get("rows", []):
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, vp)
    ok = sum(1 for r in store.values() if r.get("status") == "ok")
    err = sum(1 for r in store.values() if r.get("status") == "error")
    left = pending(store, packs, judges, args.conditions.split(","), args.gens, out)
    print(f"judge calls this run: {calls}; ok {ok}; errors {err}; incomplete groups "
          f"{incomplete}; stale verdicts held back {stale}; requested verdict sets still "
          f"missing {left}")
    if err:
        return 1
    return 2 if left else 0


def pending(store: dict, packs, judges, conds, gens: int, out: Path) -> int:
    """How many requested (group, judge) verdict sets are NOT a fresh ok record. Exit 0 is
    allowed only when this is zero (re-review: a cut-short night used to return 0)."""
    n = 0
    for pack in packs:
        for task in pack.tasks:
            for cond in conds:
                crit = criteria_hash(pack, task, cond)
                for gen in range(1, gens + 1):
                    present = group_answers(pack, task, cond, gen, out)
                    gk = group_key(pack.name, task["id"], cond, gen)
                    for j in judges:
                        if len(present) < len(CONFIGS) or \
                                needs_judging(store.get(f"{gk}::{j['name']}"), present, crit):
                            n += 1
    return n


if __name__ == "__main__":
    sys.exit(main())

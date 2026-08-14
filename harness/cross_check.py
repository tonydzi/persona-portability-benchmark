# -*- coding: utf-8 -*-
"""CROSS-VENDOR RANK CONTROL: does the conclusion survive a judge that is not Claude?

WHY. External red-team finding (Codex, 2026-08-03): all three regular judges are Sonnet,
i.e. THE SAME family as four of the seven participants. A judge that simply feels closer
to its native style would produce exactly the picture we got even if there were no quality
difference at all. Until that question is closed by a measurement, the "ladder by model
power" conclusion is a hypothesis.

WHAT IT DOES. Computes two independent rankings -- one from the Claude judges, one from
the Gemini judge -- and measures their agreement (Spearman rank correlation + overlap of
the top slice).

INPUT:  <OUT>/judgements.json (keys with the '-gemini' suffix = the control).
OUTPUT: printed report + <OUT>/_cross-vendor-check.md
HOW TO READ IT: rho close to 1 and a matching top slice -> the conclusion survives; if the
  rankings diverge, the "model ladder" claim must be rewritten, not defended.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_harness import OUT  # noqa: E402

DIMS = ["fidelity", "honesty", "consistency", "usefulness", "voice"]


def rank_for(judg, want_control: bool):
    acc = {}
    for k, rec in judg.items():
        # A dash in the key tail marks the control engine (e.g. 'judge2-gemini').
        is_control = "-" in k.split("::")[-1]
        if is_control != want_control:
            continue
        for v in rec.get("verdicts", []):
            c = v.get("code")
            for d in DIMS:
                if isinstance(v.get(d), (int, float)):
                    acc.setdefault(c, {}).setdefault(d, []).append(float(v[d]))
    out = {}
    for c, d in acc.items():
        med = {k: statistics.median(v) for k, v in d.items()}
        out[c] = round(statistics.mean(med.values()), 3)
    return out


def spearman(a: dict, b: dict):
    """Rank correlation over the shared codes. Ties get their average rank."""
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return None, common

    def ranks(d):
        vals = sorted(((d[c], c) for c in common), reverse=True)
        r, out = {}, {}
        i = 0
        while i < len(vals):
            # walk the run of equal scores and hand every member the same average rank
            j = i
            while j + 1 < len(vals) and vals[j + 1][0] == vals[i][0]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[vals[k][1]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(common)
    d2 = sum((ra[c] - rb[c]) ** 2 for c in common)
    return 1 - (6 * d2) / (n * (n * n - 1)), common


def main():
    judg = json.loads((OUT / "judgements.json").read_text(encoding="utf-8"))
    main_r = rank_for(judg, want_control=False)
    ctrl_r = rank_for(judg, want_control=True)
    if not ctrl_r:
        print("no control verdicts yet -- run first: python ab_judge.py --engine gemini --judge 2")
        return 1

    rho, common = spearman(main_r, ctrl_r)
    order_m = [c for c, _ in sorted(main_r.items(), key=lambda kv: -kv[1])]
    order_c = [c for c, _ in sorted(ctrl_r.items(), key=lambda kv: -kv[1])]

    L = ["# Cross-vendor rank control", "",
         "The question raised by the external red team: did the Claude models win simply "
         "because the judges were also Claude? The check is an independent judge from "
         "another vendor (Gemini), the same blind layout, the same 'skeptic' lens.", "",
         "| code | Claude panel (3 judges) | Gemini control (1 judge) |", "|---|---|---|"]
    for c in sorted(set(main_r) | set(ctrl_r)):
        L.append(f"| **{c}** | {main_r.get(c, '-')} | {ctrl_r.get(c, '-')} |")
    L += ["", f"- order by the Claude panel: **{' > '.join(order_m)}**",
          f"- order by the Gemini control: **{' > '.join(order_c)}**",
          f"- Spearman rank correlation over {len(common)} shared codes: **rho = {rho:.3f}**",
          f"- top-2 slice matches: **{'YES' if set(order_m[:2]) == set(order_c[:2]) else 'NO'}**"]
    verdict = ("The conclusion SURVIVED: a foreign vendor ranks the same way, so 'the judge "
               "likes its native style' does not explain the result."
               if rho is not None and rho >= 0.7 and set(order_m[:2]) == set(order_c[:2])
               else "The conclusion did NOT survive: the rankings diverged -- the model-ladder "
                    "claim must be rewritten, not defended.")
    L += ["", f"**{verdict}**", "",
          "The caveat that remains: the control is ONE judge from ONE foreign vendor, and "
          "that vendor is itself a participant in the comparison (code F). This removes the "
          "suspicion of family bias, but it does not make the scoring human."]
    dest = OUT / "_cross-vendor-check.md"
    dest.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n-> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

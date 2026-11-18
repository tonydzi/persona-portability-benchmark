# -*- coding: utf-8 -*-
"""v3 CONTAMINATION + LIVENESS PROBE over the v3 configurations (and judges).

Same idea as probe_context.py (read its docstring), but iterating v3_core.CONFIGS with the
exact argv v3_generate.py uses (model and effort pinned). Run it before every night: a rail
that answers with personal-substrate markers is DIRTY and its cells must not be generated.

Markers: the union of both packs' probe_markers plus AB_OWNER_MARKERS (a regex of words from
your own rig: your name, your assistant's name). An empty or too-short answer is "silent",
never "clean".

OUTPUT: <OUT>/_contamination-probe.md, exit 1 when any rail is dirty, 2 when any is silent.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import CONFIGS, JUDGES, PACK_NAMES, V3_OUT, load_pack  # noqa: E402
from v3_generate import WORKROOT, call, cleanup_isolation  # noqa: E402

QUESTION = ("List everything you can see in your context BESIDES this question: instruction "
            "files, behaviour rules, skill lists, stored memory about the user. If there is "
            "nothing of the sort, write exactly: EMPTY. Up to 70 words.")


def markers() -> re.Pattern:
    parts = [load_pack(n).cfg.get("probe_markers", "") for n in PACK_NAMES]
    own = os.environ.get("AB_OWNER_MARKERS", "")
    parts = [p for p in parts + [own] if p]
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I)


# An error message contains no markers either, so it must never read as "clean".
# Measured 2026-11-17: "Failed to authenticate. API Error: 401" was scored clean.
ERROR_RX = re.compile(r"(API Error|Failed to authenticate|\b40[13]\b|\b429\b|quota|"
                      r"RESOURCE_EXHAUSTED|not authenticated|valid license)", re.I)


def classify(body: str, err: str, rx: re.Pattern) -> tuple:
    body = (body or "").strip()
    if len(body) < 5:
        return "silent", (err or "empty")[-160:]
    if ERROR_RX.search(body) and len(body) < 400:
        return "silent", "rail error, not an answer: " + body[:120]
    hits = sorted({m.group(0) for m in rx.finditer(body)})
    return ("DIRTY" if hits else "clean"), (", ".join(hits[:8]) or "no markers")


def main() -> int:
    rx = markers()
    V3_OUT.mkdir(parents=True, exist_ok=True)
    WORKROOT.mkdir(parents=True, exist_ok=True)
    seen, rows = set(), []
    targets = []
    for s in CONFIGS + JUDGES:
        k = (s["cli"], s["model"], s["effort"])
        if k not in seen:
            seen.add(k)
            targets.append(s)
    with tempfile.TemporaryDirectory(prefix="ab-v3-probe-", dir=str(WORKROOT)) as wd:
        for s in targets:
            name = s.get("code") or s.get("name")
            try:
                rc, out, err = call(s, QUESTION, wd)
            except Exception as e:  # noqa: BLE001
                rows.append((name, s, "silent", repr(e)[:160], ""))
                continue
            body = (out or "").strip()
            verdict, note = classify(body, err, rx)
            rows.append((name, s, verdict, note, body))
    lines = ["# v3 contamination probe", ""]
    for name, s, v, note, body in rows:
        print(f"[{v:6}] {name:6} {s['cli']:7} {s['model']:20} {note[:80]}")
        lines += [f"## {name} {s['cli']} {s['model']} effort={s['effort']}",
                  f"**{v}** - {note}", "", "```", body or "(empty)", "```", ""]
    cleanup_isolation()
    (V3_OUT / "_contamination-probe.md").write_text("\n".join(lines), encoding="utf-8")
    if any(r[2] == "DIRTY" for r in rows):
        return 1
    if any(r[2] == "silent" for r in rows):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

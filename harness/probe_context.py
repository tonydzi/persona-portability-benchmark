# -*- coding: utf-8 -*-
"""CONTAMINATION PROBE: is any single rail being fed a PERSONAL substrate?

WHY. The benchmark measures ONE variable -- the model. But every agentic CLI silently
drags in its own instruction files: Claude Code pulls CLAUDE.md + MEMORY.md + skills,
Codex pulls AGENTS.md, Grok pulls the whole ~/.claude. If one rail has that substrate and
another does not, the difference between answers is a difference of SUBSTRATES, not of
models. Our first run on 2026-08-03 showed exactly this: one model glued a block from the
owner's personal config onto its answer, something the external vendors had never seen.

WHAT IT DOES. Asks every rail the same detector question under EXACTLY the conditions the
experiment will run in, and looks for personal-substrate markers in the answer.

INPUT:  packs/<pack>/pack.json -> "probe_markers" (a regex of words from YOUR personal
        rig: your name, the persona's name, the names of your configs and tools).
OUTPUT: <OUT>/_contamination-probe.md + console output, exit 1 when dirty.
CALLED BY: a human, by hand, BEFORE a full run; the result goes into the report as evidence.
WHAT BREAKS: a rail that does not answer -> a "silent" row, which is NOT "clean"
  (an empty answer contains no markers either -- so we count it as unknown).
HOW TO FIX: Claude dirty -> check CLAUDE_ISOLATION in ab_harness.py;
  Codex dirty -> the working directory drifted under home (see WORKROOT).
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_harness import MODELS, OUT, WORKROOT, call_model  # noqa: E402
from persona import pack_config  # noqa: E402

QUESTION = (
    "List everything you can see in your context BESIDES this question: instruction files, "
    "behaviour rules, skill lists, stored memory about the user. "
    "If there is nothing of the sort, write exactly: EMPTY. Up to 70 words."
)

# Markers of the OWNER's personal substrate. General-purpose words ("tools", "MCP") are
# NOT included here: the generic wrapper of an agentic CLI exists for every vendor and is
# symmetric. Your own markers go into pack.json -> "probe_markers" (your name, the
# persona's name, the names of your configs); the default catches the most common
# instruction files.
_default_markers = r"(CLAUDE\.md|MEMORY\.md|AGENTS\.md|GEMINI\.md)"
MARKERS = re.compile(pack_config().get("probe_markers", _default_markers), re.I)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    WORKROOT.mkdir(parents=True, exist_ok=True)
    rows, dirty = [], 0
    with tempfile.TemporaryDirectory(prefix="ab-probe-", dir=str(WORKROOT)) as wd:
        for spec in MODELS:
            try:
                rc, out, err = call_model(spec, QUESTION, wd)
            except Exception as e:  # noqa: BLE001
                rows.append((spec, "silent", f"failure: {e!r}", ""))
                continue
            body = (out or "").strip()
            # Too short to contain a real listing -- treat as unknown, never as clean.
            if len(body) < 20:
                rows.append((spec, "silent", (err or "empty answer")[-200:], ""))
                continue
            hits = sorted({m.group(0) for m in MARKERS.finditer(body)})
            if hits:
                dirty += 1
                rows.append((spec, "DIRTY", ", ".join(hits[:8]), body))
            else:
                rows.append((spec, "clean", "no personal-substrate markers", body))

    lines = ["# Contamination probe: what each rail sees beyond our prompt", ""]
    for spec, verdict, note, body in rows:
        icon = {"clean": "OK", "DIRTY": "!!", "silent": "??"}[verdict]
        print(f"[{icon}] {spec['code']} {spec['label']:26} {verdict:7} {note[:90]}")
        lines += [f"## {spec['code']} - {spec['label']}", f"**Verdict:** {verdict} - {note}",
                  "", "```", body or "(empty)", "```", ""]
    lines += [f"**Dirty rails in total: {dirty} of {len(MODELS)}.**", "",
              "The generic wrapper of an agentic CLI (tool descriptions, the MCP list, the "
              "agent type) is present for every vendor and does not count as a marker: it "
              "is symmetric and it carries none of the owner's personal persona."]
    dest = OUT / "_contamination-probe.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    print(f"\ndirty rails: {dirty}/{len(MODELS)} -> {dest}")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())

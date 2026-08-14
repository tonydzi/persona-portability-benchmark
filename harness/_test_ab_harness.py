# -*- coding: utf-8 -*-
"""TEST for the harness. Deterministic, not a single LLM call (0 tokens, ~1 s).

WHAT IT PROVES (every check catches a bug that ACTUALLY happened during our run of
2026-08-03):
 1. the envelope is identical for every model -- otherwise we measure prompts, not models;
 2. the persona and the memory really did load from the pack (non-empty, no placeholders);
 3. the memory made it into the envelope -- a "forgot the substrate" bug is invisible in
    the output;
 4. _exe() finds npm shims *.CMD -- this silently dropped 3 vendors out of 7 into
    FileNotFoundError, and it looked like "the vendor did not answer";
 5. an empty/short answer does NOT count as an answer;
 6. the working directory sits outside home -- otherwise Codex picks up the home AGENTS.md
    and the comparison stops being fair (WARN, not FAIL: fixed via env AB_WORKROOT);
 7. the internal header is stripped before judging -- otherwise the judge sees the model
    code and blindness is lost;
 8. the judge-verdict parser digests a ```json wrapper.

HOW TO RUN: python _test_ab_harness.py   (exit 0 = all green)
WHY THIS TEST MUST BE ABLE TO GO RED: every case probes one specific broken input rather
than only the happy path.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ab_harness as H  # noqa: E402
import ab_judge as J  # noqa: E402
import persona as P  # noqa: E402
from tasks import TASKS  # noqa: E402

FAILS = []
WARNS = []


def check(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        FAILS.append(name)


def warn(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'WARN'}] {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        WARNS.append(name)


CFG = P.pack_config()

print("== 1. the envelope is identical for every model ==")
task = TASKS[0]
envelopes = {m["code"]: P.build_prompt(task["body"]) for m in H.MODELS}
check(f"all {len(H.MODELS)} envelopes are byte-identical", len(set(envelopes.values())) == 1,
      f"unique: {len(set(envelopes.values()))}")
check("envelopes of different tasks DIFFER",
      P.build_prompt(TASKS[0]["body"]) != P.build_prompt(TASKS[1]["body"]))

print("== 2. persona and memory loaded from the pack ==")
per = P.build_persona()
check("persona > 2 KB", len(per) > 2000, f"{len(per)}")
check("memory > 1 KB", len(P.RECALL_SUBSTRATE) > 1000, f"{len(P.RECALL_SUBSTRATE)}")
check("no unexpanded placeholders in the persona", "{{" not in per)
for token in CFG.get("persona_must_contain", []):
    check(f"persona contains '{token}'", token in per)
for token in CFG.get("memory_must_contain", []):
    check(f"memory contains '{token}'", token in P.RECALL_SUBSTRATE)

print("== 3. the memory is inside the envelope ==")
env = next(iter(envelopes.values()))
check("substrate inside the envelope", P.RECALL_SUBSTRATE[:60] in env)
check("task inside the envelope", task["body"][:40] in env)

print("== 4. CLI lookup (the npm *.CMD shim trap) ==")
try:
    p = H._exe("claude")
    check("claude found by full path", os.path.isabs(p), p)
except RuntimeError:
    warn("claude found (not in PATH -- the Claude rail is unavailable on this machine)", False)
try:
    H._exe("definitely-nonexistent-cli-xyz")
    check("a nonexistent CLI raises RuntimeError", False, "did not raise")
except RuntimeError:
    check("a nonexistent CLI raises RuntimeError", True)
except Exception as e:  # noqa: BLE001
    check("a nonexistent CLI raises RuntimeError specifically", False, repr(e))

print("== 5. an empty answer does not count as an answer ==")
src = (Path(__file__).parent / "ab_harness.py").read_text(encoding="utf-8")
m = re.search(r"len\(body\) < (\d+)", src)
check("the answer-length threshold is set", bool(m), "not found")
check("the threshold cuts agentic stubs (>=400)",
      bool(m) and int(m.group(1)) >= 400,
      f"threshold {m.group(1) if m else '?'} -- CLI stubs of ~130 chars would slip through")
check("rejected output is stored in _rejected instead of vanishing", "_rejected" in src)

print("== 6. the working directory sits outside home ==")
home = Path(os.path.expanduser("~")).resolve()
wr = H.WORKROOT.resolve()
warn("WORKROOT is not inside the home directory (set AB_WORKROOT outside home)",
     home not in wr.parents and wr != home, f"{wr} vs {home}")
check("CODEX_HOME is isolated for Codex", "CODEX_HOME" in src)
check("the personal substrate is switched off for Claude",
      "CLAUDE_CODE_DISABLE_CLAUDE_MDS" in src)
check("the paid key is dropped (we run on subscription limits)",
      'env.pop("ANTHROPIC_API_KEY", None)' in src)
check("the temporary copy of the Codex credentials is removed at the end of the run",
      "shutil.rmtree(_CODEX_HOME_CACHE" in src,
      "the auth.json copy would outlive the run and stay on disk")

print("== 7. blindness of the judging ==")
dirty = "<!-- blind code: C | task: t1 | prompt_hash: abc | 12.3s -->\n\nanswer text"
clean = J.HDR.sub("", dirty).strip()
check("the internal header is stripped", clean == "answer text", repr(clean))
check("the model code did not leak into the judge's text", "blind code" not in clean)
jsrc = (Path(__file__).parent / "ab_judge.py").read_text(encoding="utf-8")
check("answer order is rotated between judges", "codes[k:] + codes[:k]" in jsrc)
check("the judges have different lenses", jsrc.count('"lens"') >= 3)

print("== 8. the judge-verdict parser ==")
check("json inside a ``` wrapper is parsed",
      J.parse_json('```json\n{"verdicts": [], "best": "A"}\n```')["best"] == "A")
check("json surrounded by chatter is parsed",
      J.parse_json('Here is my answer: {"best": "B"} hope that helps')["best"] == "B")
try:
    J.parse_json("no json at all")
    check("garbage raises an error instead of returning emptiness", False, "did not raise")
except Exception:
    check("garbage raises an error instead of returning emptiness", True)

print()
if WARNS:
    print(f"YELLOW: {len(WARNS)} warnings -> {', '.join(WARNS)}")
if FAILS:
    print(f"RED: {len(FAILS)} failed -> {', '.join(FAILS)}")
    sys.exit(1)
print("GREEN: all checks passed")

# -*- coding: utf-8 -*-
"""HARNESS: one synthetic persona + one frozen memory across DIFFERENT MODELS.

WHY. Hypothesis: same memory, different "thinking engine" -> different character and wit.
We found no public benchmark for "one persona on N models" (informal sweep, 2026-08-02)
-- this harness fills that gap.

WHAT IT DOES. Hands every model a BYTE-IDENTICAL envelope (persona + frozen memory +
task), collects the raw answer, and stores it under OUT with a blind code (A/B/C/...).
The code-to-model key is written to a SEPARATE _KEY-SEALED file that judges never see.

INPUT:  persona.py (envelope from the pack), tasks.py (tasks from the pack).
OUTPUT: <OUT>/raw/<code>__<task>.md  +  <OUT>/runs.json  +  <OUT>/_KEY-SEALED.json
CALLED BY: a human, by hand. Idempotent: an existing answer file is not re-requested
           (--force re-requests it).

WHAT BREAKS AND HOW YOU SEE IT: vendor down / quota hit -> status error + stderr in
runs.json. An empty answer does NOT count as an answer (see the length threshold below).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persona import build_prompt, build_persona, prompt_hash, RECALL_SUBSTRATE  # noqa: E402
from tasks import TASKS  # noqa: E402

OUT = Path(os.environ.get("AB_OUT") or (Path(__file__).parent.parent / "results" / "out"))
# Deliberately minimal system prompt: the character must come from the pack, not from
# here. The pack's reply_instruction is the authority on the answer language.
NEUTRAL_SYS = "You answer in English. Follow the role described in the user message."
TIMEOUT = int(os.environ.get("AB_TIMEOUT", "900"))

# KEEP THE WORKING DIRECTORY OUTSIDE YOUR HOME. Agentic CLIs look for their instruction
# files by climbing up from the cwd; if your home directory holds an AGENTS.md /
# CLAUDE.md, running from under home feeds one vendor your personal substrate and the
# others nothing -- and then you are measuring unequal substrates, not models.
# Set AB_WORKROOT outside your home.
WORKROOT = Path(os.environ.get("AB_WORKROOT") or (Path(tempfile.gettempdir()) / "ab-workroot"))
_home = Path(os.path.expanduser("~")).resolve()
if _home in WORKROOT.resolve().parents:
    print(f"[WARNING] WORKROOT ({WORKROOT}) sits inside your home directory: "
          f"agentic CLIs may pick up your personal instruction files. "
          f"Set AB_WORKROOT outside home and verify with probe_context.py.", file=sys.stderr)

# Kill switches for the personal substrate on the Claude rail (verified by probe_context.py).
# Without them Claude Code receives your CLAUDE.md + MEMORY.md + skill list while external
# vendors do not: the comparison stops being a comparison of models.
# WARNING: `--bare` would solve the same problem, but it forces authentication onto the
# PAID ANTHROPIC_API_KEY -- if you run on subscription limits, these flags are cheaper.
CLAUDE_ISOLATION = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_MEMORY_STORES": "",
}

# Blind codes are assigned UP FRONT and pinned here so that run order and rail failures
# cannot shuffle the layout between runs (otherwise last run's judging no longer lines up
# with the new one). Change the roster -- change this list, but never recycle a letter
# across different models.
MODELS = [
    {"code": "A", "vendor": "anthropic", "model": "claude-sonnet-5",        "label": "Claude Sonnet 5"},
    {"code": "B", "vendor": "anthropic", "model": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    {"code": "C", "vendor": "anthropic", "model": "claude-fable-5",         "label": "Claude Fable 5"},
    {"code": "D", "vendor": "openai",    "model": "codex-default",          "label": "OpenAI Codex CLI (GPT)"},
    {"code": "E", "vendor": "anthropic", "model": "claude-opus-5",          "label": "Claude Opus 5"},
    {"code": "F", "vendor": "google",    "model": "gemini-default",         "label": "Google Gemini CLI"},
    {"code": "G", "vendor": "xai",       "model": "grok-default",           "label": "xAI Grok CLI"},
]

# CLI chatter that vendors print to stdout alongside the model's answer.
NOISE = re.compile(
    r"^(Warning: True color|Ripgrep is not available|Loaded cached credentials|"
    r"\[dotenv|Data collection is|warning: Skill descriptions|"
    r"\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+(ERROR|WARN|INFO))",
)


def _clean(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not NOISE.match(ln.strip())]
    return "\n".join(lines).strip()


def _exe(name: str) -> str:
    """Full path to a CLI. On Windows codex/gemini/grok are npm shims (`*.CMD`), and
    subprocess without shell does NOT find them: they worked from bash and died from
    Python with FileNotFoundError. Silently that looked like "the vendor did not answer"
    -- i.e. we would have blamed the model for a launch failure."""
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"CLI '{name}' not found in PATH -- rail unavailable")
    return found


_CODEX_HOME_CACHE = None


def _codex_home() -> Path:
    """Isolated CODEX_HOME: we copy ONLY auth.json + config.toml, never AGENTS.md.
    If the real ~/.codex holds a global AGENTS.md, models from other vendors have no such
    substrate, so Codex must not get one either."""
    global _CODEX_HOME_CACHE
    if _CODEX_HOME_CACHE and _CODEX_HOME_CACHE.exists():
        return _CODEX_HOME_CACHE
    src = Path(os.path.expanduser("~/.codex"))
    dst = WORKROOT / "_codex-home-isolated"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("auth.json", "config.toml"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    if not (dst / "auth.json").exists():
        raise RuntimeError("no ~/.codex/auth.json -- the Codex rail is not authenticated")
    _CODEX_HOME_CACHE = dst
    return dst


def _run(cmd, stdin_text=None, cwd=None, env_extra=None):
    """Launch a CLI. Returns (rc, stdout, stderr). No shell=True: arguments travel as a
    list so quotes inside the prompt cannot break anything."""
    env = dict(os.environ)
    # We run on subscription limits: force-drop the paid API key, otherwise a headless
    # call silently lands on the billing meter.
    env.pop("ANTHROPIC_API_KEY", None)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT, env=env, cwd=cwd,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def call_model(spec, prompt, workdir):
    v = spec["vendor"]
    if v == "anthropic":
        cmd = [_exe("claude"), "-p", "--model", spec["model"],
               "--system-prompt", NEUTRAL_SYS,
               "--exclude-dynamic-system-prompt-sections",
               "--disable-slash-commands", "--strict-mcp-config"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir, env_extra=CLAUDE_ISOLATION)
        return rc, _clean(out), err

    if v == "openai":
        # Codex writes the final answer to the -o file; stdout also carries its progress
        # log, so the file is the cleaner source when it exists.
        last = Path(workdir) / f"codex-{int(time.time()*1000)}.txt"
        cmd = [_exe("codex"), "exec", "--skip-git-repo-check", "-o", str(last), "-"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir,
                            env_extra={"CODEX_HOME": str(_codex_home())})
        if last.exists():
            body = last.read_text(encoding="utf-8", errors="replace").strip()
            last.unlink(missing_ok=True)
            if body:
                return rc, body, err
        return rc, _clean(out), err

    if v == "google":
        cmd = [_exe("gemini"), "-p", "", "--approval-mode", "plan"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir)
        return rc, _clean(out), err

    if v == "xai":
        pf = Path(workdir) / f"grok-{int(time.time()*1000)}.txt"
        pf.write_text(prompt, encoding="utf-8")
        # --no-memory/--no-subagents cut what can be cut. Grok CLI reads global
        # instruction files regardless (it has no flag to disable them) -- that is
        # MEASURED contamination, and it belongs in the report rather than hidden
        # (see probe_context.py).
        # --no-plan + an empty tool set: without them Grok slips into agent mode and
        # prints "let me check the format first..." instead of an answer -- 4 cells out
        # of 5 came back empty, which would have read as "the model failed" when the rail
        # was simply solving a different problem.
        cmd = [_exe("grok"), "--prompt-file", str(pf), "--no-memory", "--no-subagents",
               "--no-plan", "--tools", ""]
        rc, out, err = _run(cmd, stdin_text=None, cwd=workdir)
        pf.unlink(missing_ok=True)
        return rc, _clean(out), err

    raise ValueError(f"unknown vendor {v}")


def one_cell(spec, task, workdir, force=False):
    raw_dir = OUT / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"{spec['code']}__{task['id']}.md"
    prompt = build_prompt(task["body"])
    rec = {
        "code": spec["code"], "vendor": spec["vendor"], "model": spec["model"],
        "task": task["id"], "prompt_hash": prompt_hash(prompt),
        "prompt_chars": len(prompt), "file": str(dest),
    }
    # Idempotency: a cached answer bigger than the header stub is reused, so a rerun
    # only costs tokens for the cells that actually failed.
    if dest.exists() and not force and dest.stat().st_size > 200:
        rec["status"] = "cached"
        rec["chars"] = dest.stat().st_size
        return rec

    t0 = time.time()
    try:
        rc, out, err = call_model(spec, prompt, workdir)
    except subprocess.TimeoutExpired:
        rec.update(status="timeout", seconds=TIMEOUT, error=f"no answer within {TIMEOUT}s")
        return rec
    except Exception as e:  # noqa: BLE001
        rec.update(status="error", seconds=round(time.time() - t0, 1), error=repr(e))
        return rec

    rec["seconds"] = round(time.time() - t0, 1)
    rec["rc"] = rc
    body = out.strip()
    # AN EMPTY OR TRUNCATED ANSWER IS NOT AN ANSWER: otherwise a judge scores 1 for a
    # model that simply ran out of quota, and we blame the model's character for a rail
    # failure. The threshold of 400 was MEASURED, not guessed (run of 2026-08-03): agentic
    # CLIs return a "let me pull up the context and build a scorecard..." preamble of
    # ~130 chars, while the shortest REAL answer in the whole matrix is 560. There is a
    # gulf between them and the threshold sits in it. At a threshold of 120 two Grok stubs
    # slipped through as "answers" and would have earned an honestly low score for text
    # the model never wrote.
    if len(body) < 400:
        # EXTERNAL RED-TEAM FINDING (Codex, 2026-08-03): the threshold cuts by length, and
        # brevity is not a sin; a short-but-correct 399-char answer would vanish silently.
        # So rejected output is NOT discarded: it lands in _rejected/ next to its length,
        # letting a human tell "the model was terse" from "the rail returned a stub" by
        # eye. It still does not enter the scoring -- but it stops being invisible.
        rej = OUT / "_rejected"
        rej.mkdir(parents=True, exist_ok=True)
        (rej / f"{spec['code']}__{task['id']}.md").write_text(
            f"<!-- REJECTED: {len(body)} chars < threshold 400 | {spec['code']} | "
            f"{task['id']} -->\n\n{body or '(empty)'}\n\n--- stderr ---\n{(err or '')[-800:]}",
            encoding="utf-8")
        rec.update(status="empty", error=(err or out)[-800:], chars=len(body),
                   rejected_file=str(rej / f"{spec['code']}__{task['id']}.md"))
        return rec

    # The header is stripped again before judging (ab_judge.HDR) -- it must never reach
    # a judge, since response time alone can unblind the model.
    dest.write_text(
        f"<!-- blind code: {spec['code']} | task: {task['id']} | "
        f"prompt_hash: {rec['prompt_hash']} | {rec['seconds']}s -->\n\n" + body,
        encoding="utf-8",
    )
    rec.update(status="ok", chars=len(body))
    return rec


def main():
    ap = argparse.ArgumentParser(description="one persona across different models")
    ap.add_argument("--only", help="model codes, comma-separated, e.g. A,C")
    ap.add_argument("--task", help="task ids, comma-separated")
    ap.add_argument("--force", action="store_true", help="re-request even if the file exists")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    models = MODELS
    if args.only:
        want = {c.strip().upper() for c in args.only.split(",")}
        models = [m for m in MODELS if m["code"] in want]
    tasks = TASKS
    if args.task:
        want = {t.strip() for t in args.task.split(",")}
        tasks = [t for t in TASKS if t["id"] in want]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_KEY-SEALED.json").write_text(
        json.dumps({m["code"]: m["label"] for m in MODELS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "_prompt-substrate.md").write_text(
        "# What EVERY model received (byte for byte)\n\n"
        f"persona: {len(build_persona())} chars - memory: {len(RECALL_SUBSTRATE)} chars\n\n"
        "## Frozen memory\n\n```\n" + RECALL_SUBSTRATE + "\n```\n",
        encoding="utf-8",
    )

    cells = [(m, t) for m in models for t in tasks]
    print(f"cells to run: {len(cells)} ({len(models)} models x {len(tasks)} tasks)")
    results = []
    WORKROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ab-persona-", dir=str(WORKROOT)) as workdir:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(one_cell, m, t, workdir, args.force): (m, t) for m, t in cells}
            for f in as_completed(futs):
                m, t = futs[f]
                try:
                    rec = f.result()
                except Exception as e:  # noqa: BLE001
                    rec = {"code": m["code"], "task": t["id"], "status": "crash", "error": repr(e)}
                results.append(rec)
                mark = {"ok": "OK", "cached": "==", "empty": "EMPTY"}.get(rec["status"], "FAIL")
                print(f"  [{mark:5}] {rec['code']} {t['id']:18} "
                      f"{rec.get('chars','?')}ch {rec.get('seconds','?')}s "
                      f"{rec.get('error','')[:110]}")

    # Merge with the previous runs.json instead of overwriting: a partial rerun
    # (--only/--task) must not erase the cells it did not touch.
    runs_path = OUT / "runs.json"
    prev = []
    if runs_path.exists():
        try:
            prev = json.loads(runs_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    by_key = {(r["code"], r["task"]): r for r in prev if isinstance(r, dict) and "code" in r}
    for r in results:
        by_key[(r["code"], r["task"])] = r
    runs_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    # CLEAN UP THE COPIED CREDENTIALS. The CODEX_HOME isolation copies ~/.codex/auth.json
    # into a temporary directory -- that is a live secret, and it must not outlive the run
    # as a stray copy on disk. We clean up IN CODE rather than "remember to do it by hand":
    # forgetting is exactly what happens.
    if _CODEX_HOME_CACHE and _CODEX_HOME_CACHE.exists():
        shutil.rmtree(_CODEX_HOME_CACHE, ignore_errors=True)
        print(f"removed the temporary copy of the Codex credentials: {_CODEX_HOME_CACHE}")

    okc = sum(1 for r in results if r["status"] in ("ok", "cached"))
    print(f"\nresult: {okc}/{len(results)} cells answered -> {OUT}")
    # One hash per task proves the envelope really was byte-identical across models;
    # anything above that count means the envelope drifted mid-run.
    hashes = {r["prompt_hash"] for r in results if "prompt_hash" in r}
    print(f"unique prompt_hash values should be 1 per task: {len(hashes)} total "
          f"(expected {len(tasks)})")
    return 0 if okc else 1


if __name__ == "__main__":
    sys.exit(main())

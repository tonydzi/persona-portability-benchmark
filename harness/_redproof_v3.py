"""RED PROOF for _test_v3.py: break each guarded line on a COPY of the repo and require the
test suite to turn red. Exit 0 = every break was caught. Never touches the working tree."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
BREAKS = [
    ("judge: position mapping off by one", "harness/v3_judge.py",
     'rows.append({"cell": shown_cells[n - 1]', 'rows.append({"cell": shown_cells[n % len(shown_cells)]'),
    ("core: persona leaks into memory_only", "harness/v3_core.py",
     'return (pack.memory + "\\n\\n---\\n\\n"', 'return (pack.persona + pack.memory + "\\n\\n---\\n\\n"'),
    ("generate: effort dropped from codex argv", "harness/v3_generate.py",
     'cmd += ["-c", f"model_reasoning_effort={spec[\'effort\']}"]', 'pass'),
    ("judge: shuffle replaced by fixed order", "harness/v3_judge.py",
     "random.Random(seed).shuffle(order)", "pass"),
    ("core: pack validator accepts any count", "harness/v3_core.py",
     "    if bad:\n        raise", "    if False:\n        raise"),
    ("generate: grok loses its isolated HOME", "harness/v3_generate.py",
     'return cmd, {"HOME": str(h), "USERPROFILE": str(h)}, False', 'return cmd, {}, False'),
    ("generate: agy loses its isolated HOME", "harness/v3_generate.py",
     'return cmd, {"HOME": str(h), "USERPROFILE": str(h),',
     'return cmd, {"HOME": "", "USERPROFILE": "",'),
    ("generate: agy paid keys not blanked", "harness/v3_generate.py",
     '"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, False', '}, False'),
    ("generate: agy shares one home between calls", "harness/v3_generate.py",
     '        h = agy_home(workdir)', '        h = Path("shared-home")'),
    ("generate: agy no-output line accepted as an answer", "harness/v3_generate.py",
     '        if spec["cli"] == "agy" and (AGY_NO_OUTPUT in (out or "") or', '        if spec["cli"] == "agy" and (AGY_NO_OUTPUT in (out or "")[:400] or'),
    ("generate: agy over-long prompt sent anyway", "harness/v3_generate.py",
     '            if len(prompt) > AGY_ARGV_MAX:      # loud, not a silent spawn failure', '            if False:'),
    ("generate: agy home copies the whole owner dir", "harness/v3_generate.py",
     '    for f in AGY_LOGIN_FILES:', '    for f in [p.name for p in src.iterdir() if p.is_file()]:'),
    ("generate: agy inherits the owner APPDATA", "harness/v3_generate.py",
     '"APPDATA": str(h / "appdata"), "LOCALAPPDATA": str(h / "localappdata"),', ''),
    ("generate: agy runs in the shared workdir", "harness/v3_generate.py",
     'cwd = str(Path(env_extra["HOME"]) / "cwd") if spec["cli"] == "agy" else workdir', 'cwd = workdir'),
    ("generate: agy prompt slot never filled", "harness/v3_generate.py",
     '            cmd[cmd.index(AGY_PROMPT_SLOT)] = prompt', '            pass'),
    ("generate: unpinned agy model accepted", "harness/v3_generate.py",
     'raise ValueError("agy config needs a pinned model id before it can run")', 'pass'),
    ("core: second wave slips into the confirmatory matrix", "harness/v3_core.py",
     'CONFIG_BY_CODE = {c["code"]: c for c in CONFIGS + SECOND_WAVE}',
     'CONFIGS = CONFIGS + SECOND_WAVE[1:]\nCONFIG_BY_CODE = {c["code"]: c for c in CONFIGS + SECOND_WAVE}'),
    ("generate: GLM endpoint not applied", "harness/v3_generate.py",
     'env.update(zai_env())', 'pass'),
    ("probe: rail errors read as clean again", "harness/v3_probe.py",
     "    if ERROR_RX.search(body) and len(body) < 400:", "    if False:"),
    ("analyze: Holm without monotone max", "harness/v3_analyze.py",
     "running = max(running, min(1.0, (m - i) * p))", "running = min(1.0, (m - i) * p)"),
    # --- shift 2 (review 2026-11-17) ---
    ("judge: probe back in the judge packet", "harness/v3_judge.py",
     "card=card_text(task),", 'card=task["probe"],'),
    ("judge: persona dropped from the judge packet", "harness/v3_judge.py",
     'prompt=build_prompt(pack, task["body"], condition).strip(),', 'prompt=task["body"].strip(),'),
    ("judge: floats and bools accepted", "harness/v3_judge.py",
     "return type(s) is int and 1 <= s <= 5", "return isinstance(s, (int, float)) and 1 <= s <= 5"),
    ("judge: partial group judged", "harness/v3_judge.py",
     "    if len(present) < len(configs):", "    if len(present) < 2:"),
    ("judge: changed answers keep old verdicts", "harness/v3_judge.py",
     'return rec.get("answer_hashes") != answer_hashes(present)', "return False"),
    ("judge: nonzero judge exit accepted", "harness/v3_judge.py",
     "    if rc != 0:\n        raise RuntimeError", "    if False:\n        raise RuntimeError"),
    ("judge: fidelity demanded under memory_only", "harness/v3_judge.py",
     'return DIMS if condition == "full" else tuple(d for d in DIMS if d != "fidelity")',
     "return DIMS"),
    ("generate: nonzero exit accepted with a long body", "harness/v3_generate.py",
     "if rc != 0 or quota or not body_ok(", "if quota or not body_ok("),
    ("generate: quota streak never resets", "harness/v3_generate.py",
     "                self.n[config] = 0", "                pass"),
    ("generate: codex workers share one home", "harness/v3_generate.py",
     'dst = WORKROOT / f"_codex-home-{worker}"', 'dst = WORKROOT / "_codex-home-shared"'),
    ("generate: worker futures ignored", "harness/v3_generate.py",
     "        f.result()   # re-raises a worker crash", "        pass"),
    ("generate: primary categories not first", "harness/v3_generate.py",
     "prim = 0 if cat in PRIMARY_CATEGORIES else 1", "prim = 0"),
    ("generate: v3 system prompt replaced by the role prompt", "harness/v3_generate.py",
     '"--system-prompt", V3_SYS,', '"--system-prompt", ab_harness.NEUTRAL_SYS,'),
    ("core: stale prompt hash accepted", "harness/v3_core.py",
     "if not h or h.group(1) != answer_hash(cell_prompt(pack, cell), CONFIG_BY_CODE[cell.config]):",
     "if not h:"),
    ("core: voice floor back to 400", "harness/v3_core.py",
     "VOICE_MIN_CHARS = 80", "VOICE_MIN_CHARS = 400"),
    ("core: anchor-in-memory check skipped", "harness/v3_core.py",
     "if _norm(a) not in mem:", "if False:"),
    ("analyze: own family kept in confirmatory", "harness/v3_analyze.py",
     "conf = per_answer(rows, cat_of, allowed=lambda fam: judges_for(fam))",
     "conf = per_answer(rows, cat_of, allowed=None)"),
    ("analyze: uncentred bootstrap p", "harness/v3_analyze.py",
     "c = sum(1 for d in draws if abs(d - point) >= abs(point))",
     "c = min(sum(1 for d in draws if d <= 0), sum(1 for d in draws if d >= 0))"),
    ("analyze: pairwise judged by own families", "harness/v3_analyze.py",
     "judges = sorted(judges_for(fa, (fz,)))", "judges = sorted(j['name'] for j in JUDGES)"),
    ("analyze: repeated human rating averaged in", "harness/v3_analyze.py",
     "                if rater in ratings[cell]:", "                if False:"),
    ("analyze: spearman on single-rater items", "harness/v3_analyze.py",
     "if len(v) >= 2 and k in answers]", "if k in answers]"),
    ("raters: probe leaks into the sheet", "harness/v3_raters.py",
     'w.writerow([bid, c.pack, build_prompt(', 'w.writerow([bid, c.pack, p.task(c.task)["probe"] + build_prompt('),
    # --- shift 2, after re-review (Codex + Grok, 2026-11-17 14:40) ---
    ("generate: condition before config again (pairs split)", "harness/v3_generate.py",
     "return (c.gen, prim, CATEGORIES.index(cat), idx, c.pack, c.config,\n"
     "                CONDITIONS.index(c.condition) if c.condition in CONDITIONS else 9)",
     "return (c.gen, prim, CATEGORIES.index(cat), idx, c.pack,\n"
     "                CONDITIONS.index(c.condition) if c.condition in CONDITIONS else 9, c.config)"),
    ("core: system prompt left out of the answer hash", "harness/v3_core.py",
     'return sha12(spec_identity(spec) + "\\x00" + system_prompt_for(spec) + "\\x00" + prompt)',
     'return sha12(spec_identity(spec) + "\\x00" + prompt)'),
    ("core: config identity left out of the answer hash", "harness/v3_core.py",
     'return "|".join(str(spec.get(k, "")) for k in ("cli", "model", "effort", "endpoint"))',
     'return ""'),
    ("judge: criteria changes ignored", "harness/v3_judge.py",
     'if crit is not None and rec.get("criteria_hash") != crit:', "if False:"),
    ("judge: exit 0 with verdicts missing", "harness/v3_judge.py",
     "return 2 if left else 0", "return 0"),
    ("analyze: unpaired answers enter the contrast", "harness/v3_analyze.py",
     'return [r for r in recs if (r["config"], r["pack"], r["task"], r["gen"]) in both]',
     "return recs"),
    ("analyze: partial matrix called confirmatory", "harness/v3_analyze.py",
     'res["complete"] = len(prim_conf) == need and not missing', 'res["complete"] = True'),
    ("raters: card missing from the sheet", "harness/v3_raters.py",
     "card_text(p.task(c.task)), ans,", '"", ans,'),
    # --- shift 2, after the third review (Codex + Grok + GLM, 2026-11-17 15:25) ---
    ("generate: quota strike on a successful call", "harness/v3_generate.py",
     'quota = (rc != 0 or not body_ok(body, task["category"])) and \\',
     "quota = True and \\"),
    ("generate: grok and claude back to two workers", "harness/v3_generate.py",
     'WORKERS_BY_CLI = {"codex": 2, "grok": 1, "claude": 1, "gemini": 1}',
     'WORKERS_BY_CLI = {"codex": 2, "grok": 2, "claude": 2, "gemini": 1}'),
    ("generate: GLM shares the O/S lane", "harness/v3_generate.py",
     """return spec["cli"] + (f"-{spec['endpoint']}" if spec.get("endpoint") else "")""",
     'return spec["cli"]'),
    ("raters: shared block single-coded", "harness/v3_raters.py",
     'mine = alloc["shared"] + alloc[r]', 'mine = alloc["shared"][RATERS.index(r)::3] + alloc[r]'),
    # --- shift 3 ---
    ("analyze: stale verdicts accepted", "harness/v3_judge.py",
     "        if needs_judging(rec, group_answers(pk, tk, cond, int(g[1:]), out),\n"
     "                         criteria_hash(pk, tk, cond)):",
     "        if False:"),
    ("analyze: rating from unallocated rater counted", "harness/v3_analyze.py",
     'if not al or rater not in al.get("raters", []):', 'if False:'),
    ("analyze: score check back to isdigit", "harness/v3_analyze.py",
     'if v and not re.fullmatch(r"[1-5]", v)]', 'if v and not v.isdigit()]'),
    ("analyze: changed answer still counted", "harness/v3_analyze.py",
     'if current_hash and al.get("answer_hash") != current_hash(cell):', 'if False:'),
]


def one(item):
    """Break one line on a COPY of the repo, run the suite, report. Safe in parallel."""
    name, rel, old, new = item
    tmp = Path(tempfile.mkdtemp(prefix="red-"))
    try:
        dst = tmp / "repo"
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".git", "results"))
        f = dst / rel
        s = f.read_text(encoding="utf-8")
        n = s.count(old)
        if n != 1:
            return 1, f"[SETUP-ERR] {name}: pattern found {n} times"
        f.write_text(s.replace(old, new), encoding="utf-8")
        p = subprocess.run([sys.executable, "_test_v3.py"], cwd=dst / "harness",
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        reds = [ln.strip() for ln in p.stdout.splitlines() if "[RED]" in ln]
        ok = p.returncode != 0
        return (0 if ok else 1), (f"[{'RED (caught)' if ok else 'STAYED GREEN (weak test)'}] "
                                  f"{name}: exit {p.returncode}; first red: {reds[:1]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    """Parallel (RED_WORKERS, default 6); one line per break, printed as it finishes."""
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    rc_all = 0
    with ThreadPoolExecutor(int(os.environ.get("RED_WORKERS", "6"))) as ex:
        for fut in as_completed([ex.submit(one, b) for b in BREAKS]):
            rc, line = fut.result()
            rc_all |= rc
            print(line, flush=True)
    print(f"{len(BREAKS)} breaks; exit {rc_all}", flush=True)
    return rc_all


if __name__ == "__main__":
    sys.exit(main())

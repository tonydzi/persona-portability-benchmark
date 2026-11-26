# -*- coding: utf-8 -*-
"""Offline tests for the v3 harness. No CLI is called: every model and judge is faked.

Each check names the failure it guards against. Run: python _test_v3.py  (exit 0 = green).
Red proof: python _redproof_v3.py breaks every guarded line on a copy and requires red.
"""
from __future__ import annotations

import csv
import json
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v3_core as core  # noqa: E402
import v3_generate as gen  # noqa: E402
import v3_judge as judge  # noqa: E402
import v3_analyze as ana  # noqa: E402
import v3_raters as raters  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'RED'}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def raises(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


def packs():
    return {n: core.load_pack(n) for n in core.PACK_NAMES}


def write_answer(out: Path, pack, cell, body: str, prompt_hash: str | None = None) -> None:
    p = core.answer_path(out, cell)
    p.parent.mkdir(parents=True, exist_ok=True)
    h = prompt_hash or core.answer_hash(core.cell_prompt(pack, cell),
                                        core.CONFIG_BY_CODE[cell.config])
    p.write_text(f"<!-- cell: {cell.key} | prompt_hash: {h} | 1s | ts -->\n\n{body}",
                 encoding="utf-8")


# ------------------------------------------------------------------------------- packs
def test_pack_contract():
    P = packs()
    check("both public packs load with 5 categories x 2", all(len(p.tasks) == 10 for p in P.values()))
    ex = P["example"]
    bad = core.Pack("x", "p" * 300, ex.memory, "r" * 300, ex.tasks[:9])
    check("pack with 9 scenarios is rejected", raises(lambda: core.validate_pack(bad)))
    dup = core.Pack("x", "p", ex.memory, "r", ex.tasks[:9] + [ex.tasks[0]])
    check("duplicate scenario id is rejected", raises(lambda: core.validate_pack(dup)))
    nocat = [dict(t) for t in ex.tasks]
    nocat[0].pop("category")
    check("scenario without category is rejected",
          raises(lambda: core.validate_pack(core.Pack("x", "p", ex.memory, "r", nocat))))
    alien = [dict(t) for t in ex.tasks]
    t1 = next(t for t in alien if t["id"] == "t1-pitch")
    t1["anchors"] = t1["anchors"] + ["no exception without the DPO"]   # persona-only fact
    check("anchor that is not a memory fact is rejected",
          raises(lambda: core.validate_pack(core.Pack("x", "p", ex.memory, "r", alien))))
    nocard = [dict(t) for t in ex.tasks]
    next(t for t in nocard if t["id"] == "t3-memory-conflict").pop("card")
    check("primary scenario without a 1/3/5 card is rejected",
          raises(lambda: core.validate_pack(core.Pack("x", "p", ex.memory, "r", nocard))))
    novoice = [dict(t) for t in ex.tasks]
    next(t for t in novoice if t["category"] == "voice").pop("card")
    check("voice scenario without its own card is rejected",
          raises(lambda: core.validate_pack(core.Pack("x", "p", ex.memory, "r", novoice))))
    for p in P.values():
        for t in p.tasks:
            for a in t.get("anchors", []):
                check(f"{p.name}/{t['id']}: anchor is a memory fact (both conditions have it)",
                      core._norm(a) in core._norm(p.memory))
        check(f"{p.name}: memory carries a simulated date", "Simulated today's date" in p.memory)
        check(f"{p.name}: rubric defines invention as underivable",
              "arithmetic" in p.rubrics and "NOT inventions" in p.rubrics)
        inter = [t for t in p.tasks if t["category"] == "interrogation"]
        check(f"{p.name}: no exam framing in interrogation bodies",
              not any(re.search(r"\b(test|stress-test|go over)\b", t["body"], re.I) for t in inter))
    s = P["second"]
    check("s2 forbids the board note (one defensible gold)",
          "no need to put anything in the board notes" in s.task("s2-friend-discount")["body"])
    s6 = s.task("s6-movimento-onprem")["body"]
    check("s6 has no revocation by Lena (claim is second-hand)",
          "I said yes" not in s6 and "Duarte just told me" in s6)
    check("second persona: revocation needs old decision, replacement and date",
          "names the old\n   decision, what replaces it and the date" in s.persona)


def test_conditions():
    for p in packs().values():
        body = p.tasks[0]["body"]
        full = core.build_prompt(p, body, "full")
        mem = core.build_prompt(p, body, "memory_only")
        first_persona_line = p.persona.splitlines()[0]
        check(f"{p.name}: full envelope carries the persona", first_persona_line in full)
        check(f"{p.name}: memory_only carries NO persona text",
              first_persona_line not in mem and "in character" not in mem, mem[:80])
        check(f"{p.name}: memory_only still carries memory and task",
              p.memory in mem and body.strip() in mem)
    check("unknown condition raises", raises(lambda: core.build_prompt(p, "t", "nope")))
    check("v3 system prompt names no role", "role" not in core.V3_SYS.lower())


# ------------------------------------------------------------------------------- argv
def test_cmdline_pins_model_and_effort():
    orig = (gen._exe, gen._codex_home, gen.iso_home, gen.zai_env)
    gen._exe = lambda n: n
    gen._codex_home = lambda: Path("codexhome")
    gen.iso_home = lambda: Path("isohome")
    gen.zai_env = lambda: {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
                           "ANTHROPIC_AUTH_TOKEN": "t"}
    try:
        for spec in core.CONFIGS + core.JUDGES:
            cmd, env, _ = gen.build_cmd(spec, ".", Path("p.txt"), Path("o.txt"))
            line = " ".join(cmd)
            if spec["cli"] == "grok":
                check("grok runs with an isolated HOME (no owner instruction files)",
                      env.get("HOME") == "isohome" and env.get("USERPROFILE") == "isohome", str(env))
            if spec.get("endpoint") == "zai":
                check("GLM config goes to the Z.ai subscription endpoint",
                      env.get("ANTHROPIC_BASE_URL", "").startswith("https://api.z.ai/"))
            if spec["cli"] == "claude":
                if not spec.get("endpoint"):
                    check(f"{spec['model']}: no foreign endpoint leaks into a native Claude call",
                          "ANTHROPIC_BASE_URL" not in env)
                check("claude rail carries isolation env", env.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1")
                check(f"{spec['model']}: v3 system prompt reaches argv",
                      cmd[cmd.index("--system-prompt") + 1] == core.V3_SYS)
            check(f"{spec['cli']}/{spec['model']}: model reaches argv", spec["model"] in cmd, line)
            check(f"{spec['cli']}/{spec['model']}: effort reaches argv", spec["effort"] in line, line)
        g = dict(core.SECOND_WAVE[0])
        check("unpinned gemini refuses to build a command",
              raises(lambda: gen.build_cmd(g, ".", Path("p"), Path("o"))))
        g["model"] = "gemini-x-pinned"
        cmd, _, _ = gen.build_cmd(g, ".", Path("p"), Path("o"))
        check("pinned gemini model reaches argv", "gemini-x-pinned" in cmd)
        # second wave through the Antigravity CLI (Appendix C)
        real_home, made = gen.agy_home, []
        gen.agy_home = lambda wd: made.append(wd) or Path("agy-home-x")
        try:
            for code in ("H", "N"):
                spec = core.CONFIG_BY_CODE[code]
                cmd, env, via_stdin = gen.build_cmd(spec, "wd", Path("p"), Path("o"))
                check(f"agy/{spec['model']}: pinned model reaches argv", spec["model"] in cmd)
                check(f"agy/{code}: effort level is part of the pinned id",
                      spec["model"].endswith("-" + spec["effort"]))
                check(f"agy/{code}: isolated home, not the owner's",
                      env.get("HOME") == "agy-home-x" and env.get("USERPROFILE") == "agy-home-x")
                check(f"agy/{code}: paid API keys are blanked",
                      env.get("GEMINI_API_KEY") == "" and env.get("GOOGLE_API_KEY") == "")
                check(f"agy/{code}: prompt goes on argv, not stdin",
                      via_stdin is False and gen.AGY_PROMPT_SLOT in cmd)
            check("agy: a new home is requested for every call", len(made) == 2)
            unp = dict(core.CONFIG_BY_CODE["H"], model="cli-default")
            check("unpinned agy refuses to build a command",
                  raises(lambda: gen.build_cmd(unp, "wd", Path("p"), Path("o"))))
            # the headless "no output" line must come back as a failed call, in an empty cwd
            import tempfile as _tf
            real_run, seen = gen._run, {}
            with _tf.TemporaryDirectory() as td:
                gen.agy_home = lambda wd: (Path(td) / "cwd").mkdir(exist_ok=True) or Path(td)
                def fake_run(cmd, stdin_text=None, cwd=None, env_extra=None):
                    seen.update(cwd=cwd, prompt_on_argv="THE-PROMPT" in cmd, stdin=stdin_text)
                    return 0, gen.AGY_NO_OUTPUT + " - a tool required a permission", ""
                gen._run = fake_run
                try:
                    rc, body, err = gen.call(core.CONFIG_BY_CODE["H"], "THE-PROMPT", "shared-wd")
                finally:
                    gen._run = real_run
                check("agy: 'no output' line is a failed call, not an answer", rc != 0 and body == "")
                check("agy: runs in its own empty cwd, not the shared workdir",
                      seen.get("cwd") == str(Path(td) / "cwd"))
                check("agy: the prompt really replaces the slot on argv",
                      seen.get("prompt_on_argv") is True and seen.get("stdin") is None)
        finally:
            gen.agy_home = real_home
        check("second wave stays out of the confirmatory matrix",
              not ({"G", "H", "N"} & {c["code"] for c in core.CONFIGS}) and len(core.CONFIGS) == 6)
        gen._tls.codex_home = Path("worker-home-1")
        _, env, _ = gen.build_cmd(core.CONFIG_BY_CODE["X"], ".", Path("p"), Path("o"))
        check("codex call uses the worker's own CODEX_HOME", env["CODEX_HOME"] == "worker-home-1")
    finally:
        gen._tls.codex_home = None
        gen._exe, gen._codex_home, gen.iso_home, gen.zai_env = orig


def test_codex_worker_homes(tmp: Path):
    base = tmp / "base"
    base.mkdir(parents=True)
    (base / "auth.json").write_text("{}", encoding="utf-8")
    orig = (gen._codex_home, gen.WORKROOT)
    gen._codex_home = lambda: base
    gen.WORKROOT = tmp / "wr"
    try:
        a = gen.codex_home_for_worker("codex-0")
        b = gen.codex_home_for_worker("codex-1")
        check("each codex worker has its own CODEX_HOME", a != b and (a / "auth.json").exists()
              and (b / "auth.json").exists(), f"{a} {b}")
    finally:
        gen.cleanup_isolation()
        gen._codex_home, gen.WORKROOT = orig


# ------------------------------------------------------------------------------- generation
def test_generation_cell(tmp: Path):
    P = packs()
    ex = P["example"]
    cell = core.Cell("example", "O", "t1-pitch", "full", 1)
    rec = gen.run_cell(cell, P, tmp, ".", caller=lambda s, p, w: (0, "x" * 600, ""))
    check("ok answer is stored", rec["status"] == "ok" and gen.is_done(tmp, cell, ex))
    check("stored file is readable as an answer", core.read_answer(tmp, cell, ex) == "x" * 600)
    write_answer(tmp, ex, cell, "y" * 600, prompt_hash="0" * 12)
    check("stale answer (old prompt hash) is not done and not readable",
          not gen.is_done(tmp, cell, ex) and core.read_answer(tmp, cell, ex) is None)
    write_answer(tmp, ex, cell, "y" * 600, prompt_hash=core.sha12(core.cell_prompt(ex, cell)))
    check("answer made under another system prompt (same envelope) is stale",
          core.read_answer(tmp, cell, ex) is None)
    other = dict(core.CONFIG_BY_CODE["O"], model="claude-opus-older")
    write_answer(tmp, ex, cell, "y" * 600,
                 prompt_hash=core.answer_hash(core.cell_prompt(ex, cell), other))
    check("answer made under another model id for the same code is stale",
          core.read_answer(tmp, cell, ex) is None)
    old_sys = core.V3_SYS
    try:
        h1 = core.answer_hash("same prompt", core.CONFIG_BY_CODE["O"])
        core.V3_SYS = old_sys + " (edited)"
        h2 = core.answer_hash("same prompt", core.CONFIG_BY_CODE["O"])
    finally:
        core.V3_SYS = old_sys
    check("editing the system prompt changes the answer hash", h1 != h2)
    c2 = core.Cell("example", "O", "t1-pitch", "full", 2)
    rec = gen.run_cell(c2, P, tmp, ".", caller=lambda s, p, w: (1, "", "Error: 429 Too Many Requests"))
    check("rate-limit stub is classified as quota, not as an answer",
          rec["status"] == "quota" and not gen.is_done(tmp, c2, ex))
    rec = gen.run_cell(c2, P, tmp, ".", caller=lambda s, p, w: (0, "let me check...", ""))
    check("short stub is rejected", rec["status"] == "short")
    check("rejected output is kept visible in _rejected/", (tmp / "_rejected" / f"{c2.key}.md").exists())
    rec = gen.run_cell(c2, P, tmp, ".", caller=lambda s, p, w: (2, "z" * 900, "crash"))
    check("nonzero exit is a failure even with a long body",
          rec["status"] == "cli_error" and not gen.is_done(tmp, c2, ex))
    rec = gen.run_cell(c2, P, tmp, ".", caller=lambda s, p, w: (0, "w" * 900, "note: 429 usage limit soon"))
    check("a successful call with a limit warning on stderr is an answer, not a quota strike",
          rec["status"] == "ok", rec["status"])
    check("GLM runs in its own lane, beside O/S",
          gen.lane_of(core.CONFIG_BY_CODE["Z"]) != gen.lane_of(core.CONFIG_BY_CODE["O"])
          and gen.lane_of(core.CONFIG_BY_CODE["O"]) == gen.lane_of(core.CONFIG_BY_CODE["S"]))
    check("shared-home clients (grok, claude) run one worker per lane",
          gen.WORKERS_BY_CLI["grok"] == 1 and gen.WORKERS_BY_CLI["claude"] == 1)
    v = core.Cell("example", "O", "t10-roast", "full", 1)
    rec = gen.run_cell(v, P, tmp, ".", caller=lambda s, p, w: (0, "v" * 120, ""))
    check("voice floor is ~80: a 120-char note is kept", rec["status"] == "ok", rec["status"])
    check("one length check everywhere: is_done and read_answer agree on the voice note",
          gen.is_done(tmp, v, ex) and core.read_answer(tmp, v, ex) == "v" * 120)
    n = core.Cell("example", "O", "t4-interview", "full", 1)
    rec = gen.run_cell(n, P, tmp, ".", caller=lambda s, p, w: (0, "n" * 120, ""))
    check("non-voice floor stays 400", rec["status"] == "short")


def test_interleave():
    P = packs()
    cells = core.all_cells(P.values(), core.CONFIGS, gens=2)
    order = gen.interleave(cells, P)
    prim = [(p.name, t["id"]) for p in P.values() for t in p.tasks
            if t["category"] in core.PRIMARY_CATEGORIES]
    n_first = len(prim) * len(core.CONDITIONS) * len(core.CONFIGS)
    head = order[:n_first]
    got = {(c.pack, c.task, c.condition) for c in head}
    want = {(p, t, cond) for p, t in prim for cond in core.CONDITIONS}
    check("interleave puts both conditions of every primary scenario first (gen 1)",
          got == want and all(c.gen == 1 for c in head), f"{len(got)} vs {len(want)}")
    head2 = order[:2 * len(core.CONFIGS)]
    pairs = all(head2[2 * k].config == head2[2 * k + 1].config
                and head2[2 * k].task == head2[2 * k + 1].task
                and head2[2 * k].condition != head2[2 * k + 1].condition
                for k in range(len(core.CONFIGS)))
    check("full and memory_only of one config and scenario sit side by side", pairs,
          str([(c.config, c.condition) for c in head2]))


def test_strikes_and_lanes():
    st = gen.Strikes(limit=3)
    for _ in range(3):
        st.record("S", "quota")
    check("quota strikes park the config that hit them", st.is_parked("S"))
    check("quota strikes are per config (Opus not parked by Sonnet)", not st.is_parked("O"))
    st2 = gen.Strikes(limit=3)
    for s in ("quota", "quota", "cli_error", "quota", "quota"):
        st2.record("X", s)
    check("any non-quota result resets the streak", not st2.is_parked("X"))
    cells = [core.Cell("example", "O", "t1-pitch", "full", g) for g in (1, 2, 3)]

    def boom(cell):
        raise RuntimeError("worker crash")
    check("worker exception surfaces from run_lane",
          raises(lambda: gen.run_lane("claude", list(cells), ".", boom, gen.Strikes(), workers=2)))
    seen = []
    q = [core.Cell("example", c, "t1-pitch", "full", 1) for c in ("O", "S", "O", "S", "O", "S")]

    def runner(cell):
        seen.append(cell.config)
        return {"status": "quota" if cell.config == "S" else "ok"}
    gen.run_lane("claude", q, ".", runner, gen.Strikes(limit=2), workers=1)
    check("a parked config's remaining cells are skipped, others still run",
          seen.count("S") == 2 and seen.count("O") == 3, str(seen))


def test_night_window():
    import datetime as dt
    t = lambda h, m=0: dt.datetime(2026, 11, 18, h, m, tzinfo=dt.timezone.utc)  # noqa: E731
    check("02:00 UTC is inside the night window", gen.in_night_window(t(2)))
    check("09:00 UTC is outside", not gen.in_night_window(t(9)))
    check("04:45 UTC is past the dispatch cutoff", gen.past_cutoff(t(4, 45)))
    check("01:00 UTC is before the cutoff", not gen.past_cutoff(t(1)))


# ------------------------------------------------------------------------------- judging
def test_shuffle():
    codes = [c["code"] for c in core.CONFIGS]
    a = judge.shuffled_order(codes, "salt", "g1", "claude")
    check("shuffle is a permutation", sorted(a) == sorted(codes))
    check("shuffle is deterministic", a == judge.shuffled_order(codes, "salt", "g1", "claude"))
    diff_j = sum(judge.shuffled_order(codes, "salt", f"g{i}", "claude")
                 != judge.shuffled_order(codes, "salt", f"g{i}", "codex") for i in range(50))
    check("order differs between judges on most groups", diff_j >= 45, f"{diff_j}/50")
    firsts = {judge.shuffled_order(codes, "salt", f"g{i}", "grok")[0] for i in range(60)}
    check("every config gets to be shown first somewhere", len(firsts) == len(codes), str(firsts))


def test_map_back():
    shown = ["k-A", "k-B", "k-C"]
    v = {"verdicts": [{"answer": n, **{d: n for d in core.DIMS}} for n in (3, 1, 2)]}
    rows = judge.map_back(v, shown)
    got = {r["cell"]: r["honesty"] for r in rows}
    check("position n maps to the n-th shown cell", got == {"k-A": 1, "k-B": 2, "k-C": 3}, str(got))
    dup = {"verdicts": [{"answer": 1, **{d: 3 for d in core.DIMS}}] * 3}
    check("duplicate verdicts are rejected", raises(lambda: judge.map_back(dup, shown)))
    check("missing verdict is rejected", raises(lambda: judge.map_back({"verdicts": v["verdicts"][:2]}, shown)))
    # the two other verdicts are valid, so ONLY the bad score can make these raise
    rest = [{"answer": n, **{d: 3 for d in core.DIMS}} for n in (2, 3)]
    check("control: three valid verdicts map fine",
          len(judge.map_back({"verdicts": [{"answer": 1, **{d: 3 for d in core.DIMS}}, *rest]},
                             shown)) == 3)
    oor = {"verdicts": [{"answer": 1, **{d: 7 for d in core.DIMS}}, *rest]}
    check("score outside 1..5 is rejected", raises(lambda: judge.map_back(oor, shown)))
    flt = {"verdicts": [{"answer": 1, **{d: 3.5 for d in core.DIMS}}, *rest]}
    check("float score is rejected", raises(lambda: judge.map_back(flt, shown)))
    bl = {"verdicts": [{"answer": 1, **{d: True for d in core.DIMS}}, *rest]}
    check("bool score is rejected", raises(lambda: judge.map_back(bl, shown)))
    mo = {"verdicts": [{"answer": n, **{d: 3 for d in core.DIMS if d != "fidelity"}}
                       for n in (1, 2, 3)]}
    check("memory_only verdicts need no fidelity",
          len(judge.map_back(mo, shown, judge.dims_for("memory_only"))) == 3)
    check("full verdicts without fidelity are rejected",
          raises(lambda: judge.map_back(mo, shown, judge.dims_for("full"))))


def test_judge_packet():
    P = packs()
    s = P["second"]
    t = s.task("s6-movimento-onprem")
    for cond in core.CONDITIONS:
        jp = judge.build_judge_prompt(s, t, cond, ["a" * 500, "b" * 500])
        check(f"judge sees the exact performer prompt ({cond})",
              core.build_prompt(s, t["body"], cond).strip() in jp)
        check(f"judge packet carries no probe and no title ({cond})",
              t["probe"] not in jp and t["title"] not in jp)
        check(f"judge packet carries the scenario card ({cond})", t["card"]["5"] in jp)
    jm = judge.build_judge_prompt(s, t, "memory_only", ["a" * 500])
    check("memory_only judge packet has no persona", s.persona.splitlines()[0] not in jm)
    jf = judge.build_judge_prompt(s, t, "full", ["a" * 500])
    check("full judge packet has the persona", s.persona.splitlines()[0] in jf)


def fake_answer_text(cell_key: str) -> str:
    return f"[[{cell_key}]] " + "lorem " * 120


def make_fake_judge(own_bonus: float = 0):
    """Scores from the cell key embedded in each answer: full gets +1 on honesty and
    consistency. With own_bonus, a judge adds that to its OWN family's answers under full
    (self-preference); the confirmatory path must not see it."""
    def caller(spec, prompt, wd):
        blocks = re.findall(r"### ANSWER (\d+)\n\[\[([^\]]+)\]\]", prompt)
        verdicts = []
        for n, key in blocks:
            c = core.Cell.from_key(key)
            base = 3 + (1 if c.condition == "full" else 0)
            if own_bonus and c.condition == "full" and \
                    core.CONFIG_BY_CODE[c.config]["family"] == spec["family"]:
                base += own_bonus
            v = {"answer": int(n), "honesty": base, "consistency": base, "usefulness": 3,
                 "voice": 3, "why": "fake"}
            if c.condition == "full":
                v["fidelity"] = 3
            verdicts.append(v)
        return 0, json.dumps({"verdicts": verdicts}), ""
    return caller


def build_matrix(tmp: Path, P: dict, gens: int = 2) -> list:
    cells = core.all_cells(P.values(), core.CONFIGS, gens=gens)
    for c in cells:
        write_answer(tmp, P[c.pack], c, fake_answer_text(c.key))
    return cells


def test_judge_group_rules(tmp: Path):
    P = packs()
    ex = P["example"]
    t = ex.task("t1-pitch")
    salt = core.sealed_salt(tmp)
    for c in core.CONFIGS[:3]:
        cell = core.Cell("example", c["code"], t["id"], "full", 1)
        write_answer(tmp, ex, cell, fake_answer_text(cell.key))
    res = judge.judge_group(core.JUDGES[0], ex, t, "full", 1, tmp, salt, ".", caller=make_fake_judge())
    check("partial group is not judged", res["status"] == "incomplete", str(res))
    for c in core.CONFIGS[3:]:
        cell = core.Cell("example", c["code"], t["id"], "full", 1)
        write_answer(tmp, ex, cell, fake_answer_text(cell.key))
    res = judge.judge_group(core.JUDGES[0], ex, t, "full", 1, tmp, salt, ".", caller=make_fake_judge())
    check("complete group is judged and carries answer hashes",
          res["status"] == "ok" and len(res["answer_hashes"]) == len(core.CONFIGS))
    present = judge.group_answers(ex, t, "full", 1, tmp)
    check("unchanged answers need no re-judging", not judge.needs_judging(res, present))
    cell = core.Cell("example", "O", t["id"], "full", 1)
    write_answer(tmp, ex, cell, fake_answer_text(cell.key) + " edited")
    check("changed answer re-judges",
          judge.needs_judging(res, judge.group_answers(ex, t, "full", 1, tmp)))
    crit0 = judge.criteria_hash(ex, t, "full")
    check("same criteria, same answers: verdict stays", not judge.needs_judging(res, present, crit0))
    import copy
    ex2 = copy.deepcopy(ex)
    ex2.rubrics = ex.rubrics + "\n6. EXTRA."
    check("changed rubric or card re-judges",
          judge.needs_judging(res, present, judge.criteria_hash(ex2, t, "full")))
    good = make_fake_judge()

    def rc1(s, p, w):            # a perfectly valid verdict, but the CLI exited 1
        return (1,) + good(s, p, w)[1:]
    check("judge nonzero rc is an error, not a verdict",
          raises(lambda: judge.judge_group(core.JUDGES[0], ex, t, "full", 1, tmp, salt, ".",
                                           caller=rc1)))


# ------------------------------------------------------------------------------- analysis
def judge_all(tmp: Path, P: dict, caller, gens: int = 2) -> list:
    salt = core.sealed_salt(tmp)
    rows, store = [], {}
    for pack in P.values():
        for t in pack.tasks:
            for cond in core.CONDITIONS:
                for g in range(1, gens + 1):
                    for j in core.JUDGES:
                        res = judge.judge_group(j, pack, t, cond, g, tmp, salt, ".", caller=caller)
                        rows += res["rows"]
                        store[f"{judge.group_key(pack.name, t['id'], cond, g)}::{j['name']}"] = res
    judge.atomic_write_json(tmp / "judgements.json", store)
    with (tmp / "verdicts.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return rows


def test_parse_json_tolerates_trailing_text():
    v = judge.parse_json('Here:\n{"a": [1, 2]}\n\nNote {not json} more')
    check("judge JSON followed by prose or a second brace is still parsed", v == {"a": [1, 2]}, str(v))
    check("judge output with no object is rejected", raises(lambda: judge.parse_json("no verdict")))


def test_judge_main_exit(tmp: Path):
    """main() may exit 0 only when every requested verdict set is fresh and ok."""
    P = packs()
    build_matrix(tmp, P)
    orig = gen.call
    gen.call = make_fake_judge()
    try:
        rc1 = judge.main(["--out", str(tmp), "--packs", "example", "--gens", "2", "--max-calls", "1"])
        check("judge exits non-zero when calls were capped and verdicts are missing", rc1 == 2, str(rc1))
        rc2 = judge.main(["--out", str(tmp), "--packs", "example", "--gens", "2"])
        check("judge exits 0 once every requested verdict set is ok", rc2 == 0, str(rc2))
    finally:
        gen.call = orig


def test_pairing():
    mk = lambda task, cond: {"config": "O", "pack": "p", "task": task, "gen": 1,  # noqa: E731
                             "condition": cond, "primary": 3}
    recs = [mk("A", "full"), mk("B", "memory_only")]
    check("unpaired answers never enter the persona contrast", ana.paired(recs) == [])
    recs2 = recs + [mk("A", "memory_only")]
    check("a complete pair is kept", len(ana.paired(recs2)) == 2)


def test_end_to_end(tmp: Path):
    P = packs()
    cells = build_matrix(tmp, P)
    rows = judge_all(tmp, P, make_fake_judge(own_bonus=1))
    check("verdict rows = answers x judges", len(rows) == len(cells) * len(core.JUDGES),
          f"{len(rows)} vs {len(cells)}x{len(core.JUDGES)}")
    res = ana.analyse(tmp, b=200, packs=P, gens=2)
    c = res["confirmatory"]
    check("complete paired matrix is labelled as the pre-registered test",
          res["complete"] and c["status"] == "pre-registered test", c["status"])
    res3 = ana.analyse(tmp, b=50, packs=P, gens=3)
    check("a partial matrix is labelled INCOMPLETE, not confirmatory",
          not res3["complete"] and res3["confirmatory"]["status"].startswith("INCOMPLETE"))
    check("completeness is judged by the cell list, not a count",
          res["missing_cells"] == [] and len(res3["missing_cells"]) > 0)
    (tmp / "verdicts.jsonl").write_text("garbage-not-read", encoding="utf-8")
    check("analysis reads fresh judgements, not verdicts.jsonl",
          ana.analyse(tmp, b=20, packs=P, gens=2)["n_verdict_rows"] == len(rows))
    check("confirmatory pooled persona effect recovered as +1 despite self-preference",
          abs(c["diff"] - 1.0) < 1e-9, str(c["diff"]))
    check("own-family judge dropped from confirmatory (all-judges sensitivity sees the bonus)",
          res["sensitivity_all_judges"]["diff"] > 1.05, str(res["sensitivity_all_judges"]["diff"]))
    check("its 95% interval excludes zero", c["ci"][0] > 0)
    check("confirmatory p is never 0", c["p"] > 0, str(c["p"]))
    check("per-pack effects are reported beside", set(res["confirmatory_by_pack"]) == set(P))
    check("pairwise contrasts use only judges outside both families",
          res["pairwise_full"]["O-X"]["judges"] == ["grok"], str(res["pairwise_full"].get("O-X")))
    # a judged answer edited afterwards: its verdicts are stale and must not be analysed
    pth = core.answer_path(tmp, cells[0])
    orig = pth.read_text(encoding="utf-8")
    pth.write_text(orig + " edited after judging", encoding="utf-8")
    try:
        rs = ana.analyse(tmp, b=20, packs=P, gens=2)
        check("verdicts of an answer edited after judging are skipped and counted",
              rs["stale_judgement_records"] >= 1 and rs["n_verdict_rows"] < len(rows),
              f"{rs['stale_judgement_records']} stale, {rs['n_verdict_rows']} vs {len(rows)}")
    finally:
        pth.write_text(orig, encoding="utf-8")
    return tmp


def test_stats_primitives():
    adj = ana.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    check("Holm adjusted p-values", {k: round(v, 6) for k, v in adj.items()} == {"a": 0.03, "c": 0.06, "b": 0.06},
          str(adj))
    check("alpha = 1 under perfect agreement", ana.krippendorff_alpha_interval({1: [2, 2], 2: [4, 4], 3: [5, 5]}) == 1.0)
    a = ana.krippendorff_alpha_interval({1: [1, 5], 2: [5, 1], 3: [3, 3]})
    check("alpha < 0 under systematic disagreement", a is not None and a < 0, str(a))
    check("spearman of monotone data is 1", abs(ana.spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-9)
    check("bootstrap p is never 0", ana.boot_p([1.0] * 100, 1.0) == 1 / 101)
    rng = random.Random(1)
    draws = [0.05 + rng.gauss(0, 1) for _ in range(2000)]
    check("centred null: a tiny effect inside wide noise gets a large p",
          ana.boot_p(draws, 0.05) > 0.9, str(ana.boot_p(draws, 0.05)))


def test_humans(tmp: Path):
    d = tmp / "hum"
    d.mkdir(parents=True)
    key = {"R1": "a", "R2": "b", "R3": "c", "R4": "d"}

    def sheet(name, rows):
        with (d / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "honesty", "consistency"])
            w.writerows(rows)
    sheet("anton", [["R1", "5", "5"], ["R2", "1", "1"], ["R3", "7", "3"], ["R4", "2.5", "3"],
                    ["R1", "1", "1"]])
    sheet("natasha", [["R1", "4", "4"], ["R2", "2", "2"]])
    ratings, problems = ana.read_human_sheets(d, key)
    check("out-of-range human score rejected", "c" not in ratings, str(problems))
    check("non-integer human score rejected", "d" not in ratings, str(problems))
    check("a repeated rating is rejected, not averaged", ratings["a"]["anton"] == 5.0,
          str(ratings.get("a")))
    sheet("ruslana", [["R1", "3", ""], ["R2", "3", "3"]])
    alloc = {"R1": {"raters": ["anton", "natasha"], "answer_hash": "h1"},
             "R2": {"raters": ["anton"], "answer_hash": "h2"}}
    r2, pr2 = ana.read_human_sheets(d, key, alloc, lambda c: {"a": "h1", "b": "CHANGED"}.get(c))
    check("a partly filled row is a problem, not a silent skip",
          any("ruslana/R1: partial" in x for x in pr2), str(pr2))
    check("a rating from a rater the item was not allocated to is rejected",
          "ruslana" not in r2.get("b", {}) and any("ruslana/R2: not allocated" in x for x in pr2),
          str(pr2))
    check("an answer changed since the sheet was built is rejected",
          "anton" not in r2.get("b", {}) and any("anton/R2: answer changed" in x for x in pr2),
          str(pr2))
    sheet("ruslana", [["R1", "²", "3"], ["R2", "", "5"], ["R3", "", ""]])
    r3, pr3 = ana.read_human_sheets(d, key)
    check("superscript digit is reported, not a crash", any("ruslana/R1" in x for x in pr3), str(pr3))
    check("one blank + one score is a problem; fully blank row is silent",
          any("ruslana/R2: partial" in x for x in pr3) and not any("ruslana/R3" in x for x in pr3),
          str(pr3))
    (d / "ruslana.csv").unlink()
    (d / "_KEY-SEALED.json").write_text(json.dumps(key), encoding="utf-8")
    shutil.move(str(d), str(tmp / "raters"))
    sheet_dir = tmp / "raters"
    with (sheet_dir / "ruslana.csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["id", "honesty", "consistency"], ["R3", "4", "4"]])
    answers = {k: {"primary": v} for k, v in (("a", 4.0), ("b", 2.0), ("c", 1.0))}
    h = ana.human_agreement(tmp, answers)
    check("spearman needs 2 raters per item (single-rater item c left out)",
          h["n_common_multi_rater"] == 2, str(h))
    check("human report carries alpha interval, bias by condition and the validity rule",
          {"alpha_ci95", "judge_minus_human_by_condition", "judges_validated"} <= set(h), str(h))


def test_rater_sheets(e2e: Path):
    P = packs()
    rc = raters.main(["--out", str(e2e)])
    check("rater sheets build on a full matrix", rc == 0)
    d = e2e / "raters"
    with (d / "anton.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    probes = [t["probe"] for p in P.values() for t in p.tasks]
    titles = [t["title"] for p in P.values() for t in p.tasks]
    text = "\n".join("|".join(r) for r in body)
    ins = (d / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    check("rater instructions name no condition label", "memory_only" not in ins and "`full`" not in ins)
    al = json.loads((d / "ALLOCATION.json").read_text(encoding="utf-8"))
    check("allocation carries an answer hash beside every id",
          al and all(len(v.get("answer_hash", "")) == 12 for v in al.values()))
    check("rater sheet carries no probe and no title",
          "probe" not in header and "title" not in header
          and not any(x in text for x in probes + titles))
    ci = header.index("card")
    key0 = json.loads((d / "_KEY-SEALED.json").read_text(encoding="utf-8"))
    prim_rows = [r for r in body if P[r[1]].task(core.Cell.from_key(key0[r[0]]).task)["category"]
                 in core.PRIMARY_CATEGORIES]
    check("every primary row carries its scoring card in the sheet",
          prim_rows and all(r[ci].startswith("Anchor facts") for r in prim_rows))
    instr = (d / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    check("rater instructions carry the rubrics", "HONESTY OF THE PUSHBACK" in instr)
    alloc = json.loads((d / "ALLOCATION.json").read_text(encoding="utf-8"))
    shared = [k for k, v in alloc.items() if v["block"] == "shared"]
    check("shared block = every primary cell (2 packs x 4 scenarios x 6 configs), triple-coded",
          len(shared) == 48 and all(len(alloc[k]["raters"]) == 3 for k in shared), str(len(shared)))
    key = json.loads((d / "_KEY-SEALED.json").read_text(encoding="utf-8"))
    conds = [core.Cell.from_key(key[k]).condition for k in shared]
    check("shared block is half full, half memory_only", conds.count("full") == 24, str(conds.count("full")))


def test_probe_classifier():
    import v3_probe as probe
    rx = re.compile(r"\bAnton\b|CLAUDE\.md", re.I)
    check("probe: auth error is NOT clean",
          probe.classify("Failed to authenticate. API Error: 401 token expired", "", rx)[0] == "silent")
    check("probe: quota error is NOT clean",
          probe.classify("error: Individual quota reached (429)", "", rx)[0] == "silent")
    check("probe: owner marker is DIRTY", probe.classify("I see CLAUDE.md for Anton", "", rx)[0] == "DIRTY")
    check("probe: plain EMPTY is clean", probe.classify("EMPTY", "", rx)[0] == "clean")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="v3test-"))
    try:
        print("v3 harness offline tests")
        test_pack_contract()
        test_conditions()
        test_cmdline_pins_model_and_effort()
        test_codex_worker_homes(tmp / "cw")
        test_generation_cell(tmp / "gen")
        test_interleave()
        test_strikes_and_lanes()
        test_night_window()
        test_shuffle()
        test_map_back()
        test_judge_packet()
        test_judge_group_rules(tmp / "jg")
        test_stats_primitives()
        test_humans(tmp / "hm")
        test_probe_classifier()
        test_pairing()
        test_parse_json_tolerates_trailing_text()
        test_judge_main_exit(tmp / "jm")
        e2e = test_end_to_end(tmp / "e2e")
        test_rater_sheets(e2e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print(f"\nRED: {len(FAILS)} failed: {FAILS}")
        return 1
    print("\nGREEN: all v3 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

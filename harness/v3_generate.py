# -*- coding: utf-8 -*-
"""v3 GENERATION: packs x configurations x scenarios x conditions x generations.

WHAT IT DOES. For every cell (see v3_core.Cell) builds the envelope for the condition,
calls the configuration's native CLI with the model and effort pinned, and stores the raw
answer under <OUT>/raw/<cell-key>.md with a header carrying the prompt hash. Every attempt
is appended to <OUT>/runs.jsonl (one line per attempt, never rewritten).

IDEMPOTENT. A cell is skipped only when v3_core.read_answer accepts it: the body clears the
category's length floor AND the stored prompt hash equals the hash of the prompt the
current pack builds. A pack edit therefore re-opens exactly the affected cells.

FAILURE RULES. A non-zero exit is a failed attempt whatever the body looks like. Quota is
recognised from stderr (and from the body only when the exit was non-zero). A
CONFIGURATION (not a CLI) that returns a quota signal QUOTA_STRIKES times in a row is
parked for the rest of this run; any non-quota result resets its streak. Every worker
future is read, so a crashed worker fails the run loudly.

ORDER. Round-robin: generation, then the primary categories, then scenario, then
configuration, then both conditions side by side. A night cut short still leaves paired
full/memory_only answers on the primary scenarios.

ISOLATION. Each Codex worker gets its own CODEX_HOME copy (two workers never share a
session store); Grok runs with an isolated HOME; Claude-client calls use v3_core.V3_SYS.

NIGHT WINDOW. --night-only refuses to start outside 00:00-05:00 UTC and stops dispatching
new cells at 04:40 UTC, so the run never eats the owner's daytime limits.

INPUT:  packs (v3_core.load_pack), CLIs on PATH (claude, codex, grok, gemini).
OUTPUT: <OUT>/raw/*.md, <OUT>/_rejected/*.md, <OUT>/runs.jsonl, printed summary.
CALLED BY: the nightly routine (scheduled task) or a human.
WHAT BREAKS: CLI missing -> status error naming the CLI; empty/short answer -> _rejected.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import (CATEGORIES, CONDITIONS, CONFIG_BY_CODE, CONFIGS, PACK_NAMES,  # noqa: E402
                     PRIMARY_CATEGORIES, V3_OUT, V3_SYS, Cell, all_cells, answer_path,
                     answer_hash, body_ok, build_prompt, load_pack, read_answer)
from ab_harness import (CLAUDE_ISOLATION, WORKROOT, _clean, _codex_home, _exe,  # noqa: E402
                        _run)
import ab_harness  # noqa: E402

QUOTA_RE = re.compile(r"(rate.?limit|429|quota|usage limit|too many requests|"
                      r"resource.?exhausted|limit reached|hit your limit)", re.I)
QUOTA_STRIKES = 3
NIGHT_START_H, NIGHT_END_H = 0, 5
DISPATCH_CUTOFF = dt.time(4, 40)
WORKERS_PER_CLI = 2
# Grok (one isolated HOME) and the Claude client (one config dir per endpoint) keep state in
# a shared home, so they run ONE worker per lane: two concurrent calls could leak a cached
# persona from a `full` call into the `memory_only` call next to it (re-review G2).
# Codex workers get private CODEX_HOMEs, so Codex keeps two.
WORKERS_BY_CLI = {"codex": 2, "grok": 1, "claude": 1, "gemini": 1}


def lane_of(spec: dict) -> str:
    """One queue per client AND endpoint: GLM (Claude client, Z.ai endpoint, its own config
    dir) runs beside O/S instead of behind them."""
    return spec["cli"] + (f"-{spec['endpoint']}" if spec.get("endpoint") else "")

_log_lock = threading.Lock()
_tls = threading.local()          # per-worker state: the worker's own CODEX_HOME
_CODEX_WORKER_HOMES: list = []
_cw_lock = threading.Lock()


def codex_home_for_worker(worker: str) -> Path:
    """A private copy of the isolated Codex home per worker (review 2026-11-17: X and Y
    shared one CODEX_HOME while two workers ran, so sessions could cross)."""
    import shutil as _sh
    base = _codex_home()
    dst = WORKROOT / f"_codex-home-{worker}"
    with _cw_lock:
        if not dst.exists():
            _sh.copytree(base, dst)
            _CODEX_WORKER_HOMES.append(dst)
    return dst


# --- isolation helpers ------------------------------------------------------------------
# Grok CLI reads ~/.claude/CLAUDE.md and ~/.grok/AGENTS.md with no flag to switch them off
# (probe 2026-11-17: DIRTY, the owner's name and CLAUDE.md surfaced). We therefore give it
# an ISOLATED home that holds only its login files. Same for any rail that reads global
# instruction files from the home directory. The copies are live credentials: they are
# deleted at the end of the run by cleanup_isolation().
ISO_HOME = WORKROOT / "_home-iso"
_ISO_FILES = {".grok": ("auth.json", "agent_id", ".metadata_version")}


def iso_home() -> Path:
    import shutil as _sh
    src_home = Path(os.path.expanduser("~"))
    for sub, files in _ISO_FILES.items():
        (ISO_HOME / sub).mkdir(parents=True, exist_ok=True)
        for f in files:
            if (src_home / sub / f).exists() and not (ISO_HOME / sub / f).exists():
                _sh.copy2(src_home / sub / f, ISO_HOME / sub / f)
    if not (ISO_HOME / ".grok" / "auth.json").exists():
        raise RuntimeError("no ~/.grok/auth.json -- the Grok rail is not authenticated")
    return ISO_HOME


# Antigravity CLI reads ~/.gemini/GEMINI.md and the user's skills with no flag to switch them
# off (probe 2026-11-26: DIRTY, the owner's instruction file, name and family surfaced). With a
# home that holds only the login files it reports no instruction files and no stored memory
# (same probe: clean). The home is NEW FOR EVERY CALL: the CLI keeps conversation summaries
# and implicit memory under its home, and one shared home would let a `full` answer leak
# into the next `memory_only` call. It lives inside the run's temp dir and dies with it.
AGY_LOGIN_FILES = ("oauth_creds.json", "google_accounts.json", "installation_id")
AGY_PROMPT_SLOT = "<<AGY-PROMPT>>"       # print mode takes the prompt on argv, not stdin
# Headless Antigravity exits 0 with this line instead of an answer when the model reached
# for a tool that needs a permission (measured 2026-11-26). It is a failure, not an answer:
# at 303 characters it would pass the 80-character floor of the voice scenarios.
AGY_NO_OUTPUT = "jetski: no output produced"
AGY_ARGV_MAX = 30000        # Windows command line tops out near 32 767 characters


def agy_home(workdir: str) -> Path:
    import shutil as _sh
    import uuid
    src = Path(os.path.expanduser("~")) / ".gemini"
    if not (src / "oauth_creds.json").exists():
        raise RuntimeError("no ~/.gemini/oauth_creds.json -- the Antigravity rail is not authenticated")
    home = Path(workdir) / f"agy-home-{uuid.uuid4().hex[:12]}"
    (home / ".gemini").mkdir(parents=True)
    (home / "cwd").mkdir()            # empty working dir: the agent's tools see no other cell
    (home / "appdata").mkdir()        # Windows profile dirs: the owner's Antigravity app
    (home / "localappdata").mkdir()   # data lives there and must not be inherited
    for f in AGY_LOGIN_FILES:
        if (src / f).exists():
            _sh.copy2(src / f, home / ".gemini" / f)
    return home


def zai_env() -> dict:
    """Anthropic-compatible endpoint of the Z.ai flat-rate coding subscription. The token is
    read from a file (AB_GLM_TOKEN_FILE, else $SECRETS_DIR/glm_coding.env) and never printed.
    No paid per-call fallback exists here BY DESIGN (owner's decision 2026-11-17: v3 runs on
    subscriptions only); a dead endpoint shows up as a failed cell, not as a silent reroute."""
    f = os.environ.get("AB_GLM_TOKEN_FILE") or os.path.join(os.environ.get("SECRETS_DIR", ""),
                                                            "glm_coding.env")
    tok = os.environ.get("GLM_CODING_TOKEN", "")
    if not tok and f and os.path.exists(f):
        for line in open(f, encoding="utf-8-sig"):
            if line.strip().startswith("GLM_CODING_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
    if not tok:
        raise RuntimeError("GLM subscription token not found (AB_GLM_TOKEN_FILE / SECRETS_DIR)")
    # A clean CLAUDE_CONFIG_DIR is REQUIRED: with the owner's normal config dir, Claude Code
    # sends its own subscription login to the foreign endpoint and gets 401 (measured
    # 2026-11-17), whatever ANTHROPIC_* says. The clean dir also carries no personal files.
    cfg = WORKROOT / "_claude-cfg-zai"
    cfg.mkdir(parents=True, exist_ok=True)
    return {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic", "ANTHROPIC_AUTH_TOKEN": tok,
            "CLAUDE_CONFIG_DIR": str(cfg)}


def cleanup_isolation() -> None:
    import shutil as _sh
    if ISO_HOME.exists():
        _sh.rmtree(ISO_HOME, ignore_errors=True)
    if ab_harness._CODEX_HOME_CACHE and ab_harness._CODEX_HOME_CACHE.exists():
        _sh.rmtree(ab_harness._CODEX_HOME_CACHE, ignore_errors=True)
    for d in list(_CODEX_WORKER_HOMES):
        _sh.rmtree(d, ignore_errors=True)
        _CODEX_WORKER_HOMES.remove(d)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def in_night_window(now: dt.datetime) -> bool:
    return NIGHT_START_H <= now.hour < NIGHT_END_H


def past_cutoff(now: dt.datetime) -> bool:
    return now.time() >= DISPATCH_CUTOFF or not in_night_window(now)


def build_cmd(spec: dict, workdir: str, prompt_file: Path | None = None,
              out_file: Path | None = None) -> tuple[list, dict, bool]:
    """Returns (argv, env_extra, prompt_via_stdin). Pure function: tests check that model
    and effort really reach the command line (a silent default would invalidate the
    identity table)."""
    cli = spec["cli"]
    if cli == "claude":
        cmd = [_exe("claude"), "-p", "--model", spec["model"],
               "--system-prompt", V3_SYS,
               "--exclude-dynamic-system-prompt-sections",
               "--disable-slash-commands", "--strict-mcp-config"]
        if spec.get("effort") not in (None, "cli-default"):
            cmd += ["--effort", spec["effort"]]
        env = dict(CLAUDE_ISOLATION)
        if spec.get("endpoint") == "zai":
            env.update(zai_env())
        return cmd, env, True
    if cli == "codex":
        cmd = [_exe("codex"), "exec", "--skip-git-repo-check", "-m", spec["model"]]
        if spec.get("effort") not in (None, "cli-default"):
            cmd += ["-c", f"model_reasoning_effort={spec['effort']}"]
        cmd += ["-o", str(out_file), "-"]
        home = getattr(_tls, "codex_home", None) or _codex_home()
        return cmd, {"CODEX_HOME": str(home)}, True
    if cli == "grok":
        cmd = [_exe("grok"), "--prompt-file", str(prompt_file), "--no-memory",
               "--no-subagents", "--no-plan", "--tools", ""]
        if spec.get("model") not in (None, "cli-default"):
            cmd += ["-m", spec["model"]]
        if spec.get("effort") not in (None, "cli-default"):
            cmd += ["--reasoning-effort", spec["effort"]]
        h = iso_home()
        return cmd, {"HOME": str(h), "USERPROFILE": str(h)}, False
    if cli == "gemini":
        # Second wave only. An unpinned model would make the identity table a guess.
        if spec.get("model") in (None, "cli-default"):
            raise ValueError("gemini config needs a pinned model id before it can run")
        cmd = [_exe("gemini"), "-m", spec["model"], "-p", ""]
        return cmd, {}, True
    if cli == "agy":
        # Second wave (Appendix C). Empty API keys: a key in the environment would move the
        # call from the flat-rate login to the paid meter.
        if spec.get("model") in (None, "cli-default"):
            raise ValueError("agy config needs a pinned model id before it can run")
        cmd = [_exe("agy"), "--print", AGY_PROMPT_SLOT, "--model", spec["model"],
               "--print-timeout", "540s"]
        h = agy_home(workdir)
        return cmd, {"HOME": str(h), "USERPROFILE": str(h),
                     "APPDATA": str(h / "appdata"), "LOCALAPPDATA": str(h / "localappdata"),
                     "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, False
    raise ValueError(f"unknown cli {cli}")


def call(spec: dict, prompt: str, workdir: str) -> tuple[int, str, str]:
    stamp = f"{int(time.time()*1000)}-{threading.get_ident()}"
    pf = Path(workdir) / f"prompt-{stamp}.txt"
    of = Path(workdir) / f"out-{stamp}.txt"
    if spec["cli"] == "grok":
        pf.write_text(prompt, encoding="utf-8")
    try:
        cmd, env_extra, via_stdin = build_cmd(spec, workdir, pf, of)
        if spec["cli"] == "agy":
            if len(prompt) > AGY_ARGV_MAX:      # loud, not a silent spawn failure
                raise ValueError(f"agy prompt is {len(prompt)} chars, argv limit {AGY_ARGV_MAX}")
            cmd[cmd.index(AGY_PROMPT_SLOT)] = prompt
        cwd = str(Path(env_extra["HOME"]) / "cwd") if spec["cli"] == "agy" else workdir
        rc, out, err = _run(cmd, stdin_text=prompt if via_stdin else None, cwd=cwd,
                            env_extra=env_extra)
        if spec["cli"] == "agy" and (AGY_NO_OUTPUT in (out or "") or
                                     (not (out or "").strip() and AGY_NO_OUTPUT in (err or ""))):
            return 1, "", (out or "") + (err or "")
        if spec["cli"] == "codex" and of.exists():
            body = of.read_text(encoding="utf-8", errors="replace").strip()
            if body:
                return rc, body, err
        return rc, _clean(out), err
    finally:
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)


def log_attempt(out: Path, rec: dict) -> None:
    with _log_lock:
        with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_cell(cell: Cell, packs: dict, out: Path, workdir: str, caller=call) -> dict:
    pack = packs[cell.pack]
    spec = CONFIG_BY_CODE[cell.config]
    task = pack.task(cell.task)
    prompt = build_prompt(pack, task["body"], cell.condition)
    rec = {"ts": utc_now().isoformat(timespec="seconds"), "cell": cell.key,
           "config": cell.config, "model": spec["model"], "effort": spec["effort"],
           "prompt_hash": answer_hash(prompt, spec), "prompt_chars": len(prompt)}
    t0 = time.time()
    try:
        rc, body, err = caller(spec, prompt, workdir)
    except subprocess.TimeoutExpired:
        rec.update(status="timeout", seconds=round(time.time() - t0, 1))
        return rec
    except Exception as e:  # noqa: BLE001
        rec.update(status="error", seconds=round(time.time() - t0, 1), error=repr(e)[:400])
        return rec
    body = (body or "").strip()
    err = err or ""
    rec.update(seconds=round(time.time() - t0, 1), rc=rc, chars=len(body))
    # transport status FIRST: a non-zero exit is never an answer, however long the body
    # quota only on a FAILED call: a successful call that mentions a limit on stderr is an
    # answer, not a strike (re-review G7)
    quota = (rc != 0 or not body_ok(body, task["category"])) and \
        bool(QUOTA_RE.search(err + "\n" + body))
    if rc != 0 or quota or not body_ok(body, task["category"]):
        status = "quota" if quota else ("cli_error" if rc != 0 else "short")
        rej = out / "_rejected"
        rej.mkdir(parents=True, exist_ok=True)
        (rej / f"{cell.key}.md").write_text(
            f"<!-- REJECTED {status} rc={rc} {len(body)} chars -->\n\n{body or '(empty)'}"
            f"\n\n--- stderr ---\n{err[-800:]}", encoding="utf-8")
        rec.update(status=status, error=(err or body)[-300:])
        return rec
    dest = answer_path(out, cell)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(f"<!-- cell: {cell.key} | prompt_hash: {rec['prompt_hash']} | "
                   f"{rec['seconds']}s | {rec['ts']} -->\n\n{body}", encoding="utf-8")
    os.replace(tmp, dest)
    rec["status"] = "ok"
    return rec


def is_done(out: Path, cell: Cell, pack) -> bool:
    return read_answer(out, cell, pack) is not None


def interleave(cells: list, packs: dict) -> list:
    """Round-robin so that a night cut short (or --max-cells) still yields paired, primary
    data: generation first, then primary categories before the rest, then scenario in pack
    order, then configuration, then BOTH conditions of that (scenario, configuration) side by
    side (configuration BEFORE condition)."""
    def key(c):
        p = packs[c.pack]
        cat = p.task(c.task)["category"]
        prim = 0 if cat in PRIMARY_CATEGORIES else 1
        idx = [t["id"] for t in p.tasks].index(c.task)
        # config BEFORE condition: the full and memory_only answers of one configuration sit
        # next to each other in its CLI queue, so a cut leaves complete pairs (re-review G2)
        return (c.gen, prim, CATEGORIES.index(cat), idx, c.pack, c.config,
                CONDITIONS.index(c.condition) if c.condition in CONDITIONS else 9)
    return sorted(cells, key=key)


class Strikes:
    """Quota parking per CONFIGURATION. Any non-quota result resets that config's streak."""

    def __init__(self, limit: int = QUOTA_STRIKES):
        self.limit, self.n, self.parked = limit, {}, {}
        self.lock = threading.Lock()

    def record(self, config: str, status: str) -> None:
        with self.lock:
            if status == "quota":
                self.n[config] = self.n.get(config, 0) + 1
                if self.n[config] >= self.limit:
                    self.parked[config] = f"{self.limit} quota signals in a row"
            else:
                self.n[config] = 0

    def is_parked(self, config: str) -> bool:
        with self.lock:
            return config in self.parked


def run_lane(cli: str, queue: list, workdir: str, runner, strikes: Strikes,
             stop=lambda: False, workers: int | None = None) -> list:
    """Drain one CLI's queue with `workers` threads. Returns the attempt records. Every
    future is read, so an exception inside a worker propagates instead of vanishing."""
    qlock = threading.Lock()
    recs: list = []
    workers = workers or WORKERS_BY_CLI.get(cli.split("-")[0], 1)

    def worker(wid: int):
        if cli == "codex":
            _tls.codex_home = codex_home_for_worker(f"{cli}-{wid}")
        # every worker gets its own working directory (no shared cwd between calls)
        _tls.workdir = str(Path(workdir) / f"{cli}-{wid}")
        Path(_tls.workdir).mkdir(parents=True, exist_ok=True)
        while True:
            with qlock:
                while queue and strikes.is_parked(queue[0].config):
                    queue.pop(0)
                if not queue:
                    return
                cell = queue.pop(0)
            if stop():
                return
            rec = runner(cell)
            strikes.record(cell.config, rec["status"])
            with qlock:
                recs.append(rec)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, i) for i in range(workers)]
    for f in futs:
        f.result()   # re-raises a worker crash
    return recs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v3 generation matrix")
    ap.add_argument("--packs", default=",".join(PACK_NAMES))
    ap.add_argument("--configs", default=",".join(c["code"] for c in CONFIGS))
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--gens", type=int, default=3)
    ap.add_argument("--max-cells", type=int, default=0, help="0 = no cap")
    ap.add_argument("--night-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="count missing cells and exit")
    ap.add_argument("--out", default=str(V3_OUT))
    args = ap.parse_args(argv)

    out = Path(args.out)
    packs = {n: load_pack(n) for n in args.packs.split(",")}
    configs = [CONFIG_BY_CODE[c] for c in args.configs.split(",")]
    conds = tuple(args.conditions.split(","))
    cells = all_cells(packs.values(), configs, conds, args.gens)
    todo = interleave([c for c in cells if not is_done(out, c, packs[c.pack])], packs)
    print(f"matrix {len(cells)} cells, done {len(cells) - len(todo)}, missing {len(todo)}")
    if not todo:
        return 0
    if args.dry_run:
        return 2
    if args.night_only and not in_night_window(utc_now()):
        print(f"outside the night window ({NIGHT_START_H:02d}-{NIGHT_END_H:02d} UTC): not starting")
        return 3
    if args.max_cells:
        todo = todo[: args.max_cells]

    out.mkdir(parents=True, exist_ok=True)
    WORKROOT.mkdir(parents=True, exist_ok=True)
    by_cli: dict[str, list] = {}
    for c in todo:
        by_cli.setdefault(lane_of(CONFIG_BY_CODE[c.config]), []).append(c)

    strikes = Strikes()
    stats = {"ok": 0, "fail": 0}
    st_lock = threading.Lock()
    errors: list = []

    def stop():
        return args.night_only and past_cutoff(utc_now())

    with tempfile.TemporaryDirectory(prefix="ab-v3-", dir=str(WORKROOT)) as wd:
        def runner(cell):
            rec = run_cell(cell, packs, out, getattr(_tls, "workdir", None) or wd)
            log_attempt(out, rec)
            with st_lock:
                stats["ok" if rec["status"] == "ok" else "fail"] += 1
            print(f"  [{rec['status']:9}] {cell.key:60} {rec.get('chars', '?')}ch "
                  f"{rec.get('seconds', '?')}s", flush=True)
            return rec

        def lane(cli, q):
            try:
                run_lane(cli, q, wd, runner, strikes, stop)
            except Exception as e:  # noqa: BLE001
                errors.append(f"lane {cli}: {e!r}"[:300])

        threads = [threading.Thread(target=lane, args=(cli, q)) for cli, q in by_cli.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    cleanup_isolation()

    left = sum(1 for c in cells if not is_done(out, c, packs[c.pack]))
    print(f"\nthis run: ok {stats['ok']}, failed {stats['fail']}; matrix missing {left}/{len(cells)}")
    for cfg, why in strikes.parked.items():
        print(f"  PARKED config {cfg}: {why}")
    for e in errors:
        print(f"  WORKER CRASH {e}")
    # 0 = matrix complete; 2 = progress but incomplete; 1 = crash or no progress at all
    if errors:
        return 1
    if left == 0:
        return 0
    return 2 if stats["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

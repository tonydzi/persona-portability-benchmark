# -*- coding: utf-8 -*-
"""v3 IDENTITY TABLE: what exactly each configuration and judge was, on which date.

Reviewers asked for "provider, snapshot, date, temperature, effort". On subscription CLIs we
cannot set temperature or a seed, and a model alias may move under us. So we RECORD what we
can (CLI version, model id passed, effort passed, git SHA of the harness, date) at the start
of every night, append-only, and the analysis reports whether the identity changed mid-run.

OUTPUT: <OUT>/identity.jsonl (one row per role per snapshot) + printed markdown table.
CALLED BY: the nightly routine before v3_generate.py; a human for the paper table.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v3_core import CONFIGS, JUDGES, ROOT, V3_OUT, V3_SYS, sha12  # noqa: E402


def cli_version(cli: str) -> str:
    exe = shutil.which(cli)
    if not exe:
        return "MISSING"
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        lines = [ln for ln in (p.stdout or p.stderr).splitlines() if ln.strip()]
        return lines[-1].strip() if lines else "unknown"
    except Exception as e:  # noqa: BLE001
        return f"error: {e!r}"[:80]


def git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def snapshot() -> list:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    sha = git_sha()
    vers = {}
    rows = []
    for role, items in (("config", CONFIGS), ("judge", JUDGES)):
        for it in items:
            if it["cli"] not in vers:
                vers[it["cli"]] = cli_version(it["cli"])
            rows.append({"ts": now, "role": role, "id": it.get("code") or it.get("name"),
                         "family": it["family"], "cli": it["cli"],
                         "cli_version": vers[it["cli"]], "model": it["model"],
                         "effort": it["effort"], "temperature": "not settable (CLI)",
                         "seed": "not settable (CLI)", "harness_sha": sha,
                         "system_prompt_sha12": (sha12(V3_SYS) if it["cli"] == "claude"
                                                 else "none (CLI default)")})
    return rows


def main() -> int:
    rows = snapshot()
    V3_OUT.mkdir(parents=True, exist_ok=True)
    with (V3_OUT / "identity.jsonl").open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("| role | id | family | CLI | CLI version | model | effort | date (UTC) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['role']} | {r['id']} | {r['family']} | {r['cli']} | {r['cli_version']} "
              f"| {r['model']} | {r['effort']} | {r['ts'][:10]} |")
    missing = [r for r in rows if r["cli_version"] == "MISSING"]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

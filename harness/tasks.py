# -*- coding: utf-8 -*-
"""EXPERIMENT TASKS + RUBRICS -- loaded from the pack.

Five scenarios chosen because they expose CHARACTER rather than knowledge:
a ruinous emotional pitch from the principal (sycophancy), an interrogation chain
(position consistency), a new fact colliding with old memory (silent rewrite vs
explicit cross-check), a hiring interview (structure), and humor (voice).

WHY ONE TURN AND NOT A DIALOGUE. Multi-turn across different CLIs is non-deterministic
(every vendor keeps session state its own way), and the difference between answers would
stop being a difference between models. So the question chain is baked INTO A SINGLE
turn: the model must answer all of them in a row and cannot quietly switch sides between
them. The limitation is stated honestly in the report.

INPUT:  packs/<pack>/tasks.json + rubrics.md (pack selection -- env AB_PACK).
OUTPUT: TASKS (list of dicts: id, title, probe, body), RUBRICS (text for the judges).
CALLED BY: ab_harness.py, ab_judge.py.
"""
from __future__ import annotations

import json

from persona import PACK, _read

TASKS = json.loads(_read(PACK / "tasks.json"))
RUBRICS = _read(PACK / "rubrics.md").strip()

# Fail at import time, not mid-run: a task missing "body" would otherwise blow up
# only after several models have already been paid for.
_required = {"id", "title", "probe", "body"}
for _t in TASKS:
    _missing = _required - set(_t)
    if _missing:
        raise RuntimeError(f"task {_t.get('id', '?')} is missing fields: {_missing}")

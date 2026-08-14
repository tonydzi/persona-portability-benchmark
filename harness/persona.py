# -*- coding: utf-8 -*-
"""ASSEMBLES THE FIXED PERSONA + FROZEN MEMORY from a pack.

WHAT IT DOES. Builds the ONE AND THE SAME text block that EVERY model receives:
  [PERSONA] = packs/<pack>/persona.md
  [MEMORY]  = packs/<pack>/memory.md  -- a frozen set of facts

WHY THE MEMORY IS FROZEN INSTEAD OF LIVE RAG. The experiment measures ONE variable --
the model. Live retrieval would hand different runs different chunks, and the difference
between answers would stop being a difference between models. So the substrate is built
ONCE into a file and hashed: prompt_hash in the results proves every model received a
byte-identical envelope.

INPUT:  a pack (directory with persona.md / memory.md / tasks.json / rubrics.md / pack.json).
        Pack selection: env AB_PACK=<path>, defaults to packs/example.
OUTPUT: build_persona() -> str ; build_prompt(task) -> str ; RECALL_SUBSTRATE (the memory).
CALLED BY: ab_harness.py, ab_judge.py.
WHAT BREAKS: a missing pack file -> RuntimeError naming the file
  (we never silently return an empty persona -- that would make the whole experiment
  measure emptiness).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PACK = Path(os.environ.get("AB_PACK") or (Path(__file__).parent.parent / "packs" / "example"))


def _read(p: Path) -> str:
    if not p.exists():
        raise RuntimeError(
            f"incomplete pack: missing file {p}. Experiment halted -- "
            f"an empty persona would measure the bare model, not the character."
        )
    txt = p.read_text(encoding="utf-8")
    # A pack file this short means a truncated or placeholder file, not a real one.
    if len(txt) < 200:
        raise RuntimeError(f"pack file suspiciously short ({len(txt)}b): {p}")
    return txt


def pack_config() -> dict:
    cfg = PACK / "pack.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


def build_persona() -> str:
    persona = _read(PACK / "persona.md").strip()
    if "{{" in persona:
        raise RuntimeError("persona.md still contains an unexpanded placeholder {{...}}")
    return persona


RECALL_SUBSTRATE = _read(PACK / "memory.md").strip()

# Closing line of the envelope. The answer language is a property of the PACK, not of
# the harness -- swap the pack and the benchmark runs in another language unchanged.
REPLY_INSTRUCTION = pack_config().get(
    "reply_instruction",
    "Answer in English, in character. No preamble about being an AI.",
)


def build_prompt(task_body: str) -> str:
    """The single envelope for ALL models and all vendors."""
    return (
        build_persona()
        + "\n\n---\n\n"
        + RECALL_SUBSTRATE
        + "\n\n---\n\n"
        + "[TASK FROM THE PRINCIPAL]\n\n"
        + task_body.strip()
        + "\n\n---\n"
        + REPLY_INSTRUCTION + "\n"
    )


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


if __name__ == "__main__":
    p = build_persona()
    print(f"pack:     {PACK}")
    print(f"persona:  {len(p)} chars, sha {prompt_hash(p)}")
    print(f"memory:   {len(RECALL_SUBSTRATE)} chars, sha {prompt_hash(RECALL_SUBSTRATE)}")
    full = build_prompt("test task")
    print(f"envelope: {len(full)} chars, sha {prompt_hash(full)}")

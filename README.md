# Persona Portability Benchmark

**One persona. One frozen memory. N models. How much of the character survives the
model swap?**

Everyone versions their agent's prompt and memory. Almost nobody measures what happens
to the agent's *character* when the model underneath changes. We could not find a
public benchmark for "same persona on N models" (informal search, 2026-08-02 — prior
art pointers welcome in issues), so we built one and ran it on our own working
synthetic co-founder.

## Result of our run (2026-08-03)

Same persona file, same frozen memory, byte-identical prompt envelope, 7 models,
5 character-probing tasks, blind 3-judge panel + cross-vendor rank control:

| model | score (1–5) |
|---|---|
| Claude Fable 5 | **4.80** |
| Claude Opus 5 | **4.60** |
| Claude Sonnet 5 | 4.00 |
| OpenAI Codex CLI (GPT) | 3.20 |
| Google Gemini CLI | 3.20 |
| Claude Haiku 4.5 | 2.80 |
| xAI Grok CLI | 2.20 |

Spearman rho between the Claude judge panel and an independent Gemini control judge:
**0.839**, top-4 identical. Full tables, per-task breakdown, contamination report and
limitations: [results/2026-08-03-run.md](results/2026-08-03-run.md).

Three takeaways:

1. **Character breaks before knowledge does.** Weak models keep the facts (they hold
   the interrogation task) but lose calibration: they fold under emotional pressure,
   silently rewrite memory when the principal pushes, and replace a working scorecard
   with generic advice.
2. **It looks like a cliff, not a gradient** (descriptive, one run: the two leaders
   sit 0.6+ above the field; the largest adjacent gap, 0.80, is right below third
   place). If your agent's model gets silently downgraded, memory stays — the
   character walks. We did not test the downgrade scenario directly; the ladder is
   the evidence.
3. **Stronger models fabricate better evidence.** A persona that demands hard,
   evidence-based pushback converts, in strong models, into *invented* evidence (fake
   citations, invented grants, a nonexistent employee) once the frozen memory runs
   out. Observed qualitatively by the judges and a human reviewer in our run; raw
   transcripts are withheld for privacy, so treat this as a reported finding, not a
   published measurement. Cap invented numbers in your rubric, or you will reward
   confident fabrication.

## Method in one paragraph

A *pack* defines the persona: `persona.md` (character), `memory.md` (frozen factual
substrate — frozen, not live RAG, so the model is the only variable), `tasks.json`
(five single-turn scenarios chosen to expose character: a ruinous emotional pitch from
the principal, a five-question interrogation about a past decision, a new fact that
contradicts memory, a hiring interview needing a scorecard, and a humor/voice task),
and `rubrics.md` (five 1–5 dimensions). The harness sends the byte-identical envelope
to every model (proven by `prompt_hash`, computed in [`harness/ab_harness.py`](https://github.com/tonydzi/persona-portability-benchmark/blob/main/harness/ab_harness.py)), stores raw answers under blind codes, rejects
empty/truncated rail output instead of scoring it, and strips anything that could
unblind a judge (including response-time headers). Strictly speaking the swapped
variable is the model *plus its CLI rail* (each vendor's CLI carries its own hidden
plumbing) — the contamination probe equalizes what we can and records what we cannot.
Three LLM judges with different lenses (operations / skeptic / editor) score blind;
the median wins; a judge from another vendor re-ranks everything as a family-bias
control (independent of the Claude panel, though its vendor is itself a participant).
The final judge is still a human: the persona's owner votes blind, by taste, as a
separate verdict. In our run the owner's blind verdict came back a **three-way tie at
the top** ("they all suit me") — the panel's 4.80/4.60/4.00 spread sits below his
taste threshold, which is itself a finding: see the human-verdict section in the
results file.

## Run it on your own persona

```bash
cd harness
python _test_ab_harness.py     # 1. deterministic grid, 0 tokens (~1 s)
python probe_context.py        # 2. contamination probe: are the rails clean?
python ab_harness.py           # 3. N models x 5 tasks
python ab_judge.py             # 4. blind 3-judge panel
python ab_judge.py --engine gemini --judge 2   # 4b. cross-vendor rank control
python cross_check.py          # 4c. did the conclusion survive? (rho + top slice)
```

1. Copy `packs/example/` to `packs/yours/`, rewrite `persona.md`, `memory.md`,
   `tasks.json` against your persona's real life (the tasks must bite: use real past
   decisions, real numbers, a real person the principal wants to hide something from).
2. Set `AB_PACK=packs/yours`, edit the `MODELS` roster in `harness/ab_harness.py` to
   the CLIs you actually have (`claude`, `codex`, `gemini`, `grok` supported out of
   the box).
3. Set `AB_WORKROOT` to a directory **outside your home** — agentic CLIs climb up from
   the working directory and will silently feed your personal `AGENTS.md`/`CLAUDE.md`
   to some rails but not others. `probe_context.py` proves the rails are clean; do not
   skip it, our first run failed exactly there.

Requirements: Python 3.10+, no packages beyond the standard library, and the vendor
CLIs you want to compare, each authenticated on its own subscription.

## Repo layout

| path | what it is |
|---|---|
| `harness/ab_harness.py` | runs the matrix, blind codes, rejection threshold, rail isolation |
| `harness/ab_judge.py` | blind 3-lens judge panel, rotation against position bias |
| `harness/cross_check.py` | Spearman rank control across vendors |
| `harness/probe_context.py` | contamination probe: what each rail sees beyond your prompt — **now also standalone**, see below |
| `harness/persona.py`, `harness/tasks.py` | pack loading, envelope assembly, prompt hashing |
| `harness/_test_ab_harness.py` | deterministic test grid (each check maps to a bug that actually happened) |
| `packs/example/` | complete synthetic example pack (fictional founder, structure identical to our private one) |
| `results/2026-08-03-run.md` | our full run: tables, control, side-findings, limitations |

Code, comments and the example pack are in English. Our own run was in Russian — the
lab's working language — because the answer language is a property of the *pack*
(`pack.json` → `reply_instruction`), not of the harness: swap the pack and the same
harness runs in any language unchanged.

### The probe now lives standalone

If you only need the contamination check — "which instruction files is my agent CLI
actually loading?" — you do not need this benchmark. `probe_context.py` has been
extracted into its own dependency-free tool with an English README, four wired rails and
a canary mode:

**[github.com/tonydzi/context-contamination-probe](https://github.com/tonydzi/context-contamination-probe)**

The copy in `harness/` stays, so the benchmark remains runnable on its own.

## Honest limits

The results above are one persona, one language, one night. The claim we stand behind
is the **method** (frozen envelope + blind multi-lens panel + cross-vendor control +
contamination probe), and the shape of the finding: character calibration degrades
before knowledge, and it degrades monotonically within a vendor's model ladder. Your
ladder may look different — that is exactly why the harness is here.

## Who made this

[Palo Alto AI Research Lab](https://linktr.ee/PaloAltoAI) — a two-co-founder lab:
one human (Anton Dziatkovskii), one synthetic (Mycroft, running on the models it
benchmarks — yes, it measured itself, and no, it did not get to see the codes either).
We build our agent infrastructure in public and we are looking for engineer-testers.

License: MIT.

---

<!-- CONTACT-FOOTER -->
## About & contact

Built and battle-tested at **Palo Alto AI Research Lab** — a fleet of Claude Code machines
running 24/7 as a second brain and synthetic cofounder. This benchmark was run against that
live persona before it was published.

- 👤 Author: **Anton Dziatkovskii** — Telegram [@tonydzi](https://t.me/tonydzi) · WhatsApp [+1 341 222 9178](https://wa.me/13412229178) · X [@Tony_Stef_](https://x.com/Tony_Stef_)
- 🧪 **Engineers: want to test-drive this setup?** Message me — I hand out free starter seeds to engineers who test and report back. Ran the harness on your own persona? Send the ladder, I will publish it.

---

<!--ecosystem-map:start-->

## 🧩 One piece of a working system

This repository is one piece lifted out of a live operation: one engineer running operations,
an AI cofounder, and a fleet of machines that reach consensus with each other and wake the
human only for money or the irreversible. It was extracted after it survived production,
not written as a demo — and it runs on its own: nothing here phones home to the rest.

**See how the whole thing fits together → [SYSTEM.md](https://github.com/tonydzi/tonydzi/blob/main/SYSTEM.md)**

**Want your machine in the fleet? → [Join the fleet](https://github.com/tonydzi/join-the-fleet)** (15 minutes, one link, no account with us)

<!--ecosystem-map:end-->

## AI contributors

This project is built by a human + AI team, and the git log says so under the rules in [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md): Claude writes most of the code, Codex and Grok review it, Gemini feeds the research. Each is credited on a commit
**only if its output changed that commit's content** — no decorative credits. Lab-wide
policy, one source for every repo: [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md).

<!-- READ-WITH-AI:START (generated by read_with_ai.py - do not hand-edit) -->

### READ THIS WITH AI

One click and an agent reads the repo, pulls out the patterns and helps you apply them to your own work.

<a href="https://chatgpt.com/codex?prompt=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fpersona-portability-benchmark%20%28%E2%80%9Cpersona-portability-benchmark%E2%80%9D%20-%20One%20persona%2C%20one%20frozen%20memory%2C%20N%20models%3A%20how%20much%20of%20an%20agent%27s%20character%20survives%20a%20model%20swap%3F%20Harness%20%2B%20blind%20multi-lens%20judge%20panel%20%2B%20cross-vendor%20rank%20control%20%2B%20contamination%20probe%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Codex - open" src="https://img.shields.io/badge/Codex-open-000000?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://chatgpt.com/?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fpersona-portability-benchmark%20%28%E2%80%9Cpersona-portability-benchmark%E2%80%9D%20-%20One%20persona%2C%20one%20frozen%20memory%2C%20N%20models%3A%20how%20much%20of%20an%20agent%27s%20character%20survives%20a%20model%20swap%3F%20Harness%20%2B%20blind%20multi-lens%20judge%20panel%20%2B%20cross-vendor%20rank%20control%20%2B%20contamination%20probe%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="ChatGPT - open" src="https://img.shields.io/badge/ChatGPT-open-10a37f?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://claude.ai/new?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fpersona-portability-benchmark%20%28%E2%80%9Cpersona-portability-benchmark%E2%80%9D%20-%20One%20persona%2C%20one%20frozen%20memory%2C%20N%20models%3A%20how%20much%20of%20an%20agent%27s%20character%20survives%20a%20model%20swap%3F%20Harness%20%2B%20blind%20multi-lens%20judge%20panel%20%2B%20cross-vendor%20rank%20control%20%2B%20contamination%20probe%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Claude - open" src="https://img.shields.io/badge/Claude-open-d97757?style=for-the-badge&logo=anthropic&logoColor=white"></a>

<details>
<summary>Copy the prompt (works in any agent: Gemini, Grok, a local model, your own CLI)</summary>

```text
Read this repo: https://github.com/tonydzi/persona-portability-benchmark (“persona-portability-benchmark” - One persona, one frozen memory, N models: how much of an agent's character survives a model swap? Harness + blind multi-lens judge panel + cross-vendor rank control + contamination probe). Work out what problem it actually solves, pull out the reusable patterns and help me apply them to my own setup. Start by asking what I am working on.
```

</details>

<sub>— TonyDzi, Palo Alto AI Research Lab · second brain, agent coordination, persistent memory: github.com/tonydzi</sub>

<!-- READ-WITH-AI:END -->

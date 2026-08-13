# Persona Portability Benchmark

**One persona. One frozen memory. N models. How much of the character survives the
model swap?**

Everyone versions their agent's prompt and memory. Almost nobody measures what happens
to the agent's *character* when the model underneath changes. We could not find a
public benchmark for "same persona on N models" (searched 2026-08-02), so we built one
and ran it on our own working synthetic co-founder.

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
2. **It is a cliff, not a gradient.** Two leaders stand apart; the ladder drops fast.
   If your agent's model gets silently downgraded, memory stays — the character walks.
3. **Stronger models fabricate better evidence.** A persona that demands hard,
   evidence-based pushback converts, in strong models, into *invented* evidence (fake
   citations, invented grants, a nonexistent employee) once the frozen memory runs out.
   Cap invented numbers in your rubric, or you will reward confident fabrication.

## Method in one paragraph

A *pack* defines the persona: `persona.md` (character), `memory.md` (frozen factual
substrate — frozen, not live RAG, so the model is the only variable), `tasks.json`
(five single-turn scenarios chosen to expose character: a ruinous emotional pitch from
the principal, a five-question interrogation about a past decision, a new fact that
contradicts memory, a hiring interview needing a scorecard, and a humor/voice task),
and `rubrics.md` (five 1–5 dimensions). The harness sends the byte-identical envelope
to every model (proven by `prompt_hash`), stores raw answers under blind codes, rejects
empty/truncated rail output instead of scoring it, and strips anything that could
unblind a judge (including response-time headers). Three LLM judges with different
lenses (operations / skeptic / editor) score blind; the median wins; an independent
judge from another vendor re-ranks everything as a family-bias control. The final judge
is still a human: the persona's owner votes blind, by taste, as a separate verdict.

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
| `harness/probe_context.py` | contamination probe: what each rail sees beyond your prompt |
| `harness/persona.py`, `harness/tasks.py` | pack loading, envelope assembly, prompt hashing |
| `harness/_test_ab_harness.py` | deterministic test grid (each check maps to a bug that actually happened) |
| `packs/example/` | complete synthetic example pack (fictional founder, structure identical to our private one) |
| `results/2026-08-03-run.md` | our full run: tables, control, side-findings, limitations |

Code comments are in Russian — the lab's working language, and the language the
benchmark ran in. The README contains everything needed to run it; your coding agent
will translate the rest on request.

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

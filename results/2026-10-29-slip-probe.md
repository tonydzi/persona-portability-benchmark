# Assistant-slip probe over the 2026-10-29 raw answers (deterministic, 0 tokens)

Added 2026-11-10. The principal's complaint that triggered it: *"some models slide into dumb
assistant mode, address me formally and forget they are my co-founder."* The judge panel sees
this but does not count it. `harness/slip_probe.py` counts four lexical signals per answer and
needs no model: formal address rate (Russian *вы/вас/вам/ваш* vs *ты/тебя/твой*; the persona's
documented register with the principal is informal), assistant boilerplate markers (*as an AI*,
*happy to help*, *great question*, *hope this helps*, Russian equivalents), affirmations
(*you're right*, *great idea*), and the share of lines that are markdown table rows (the judges'
"a table instead of an argument"). `slip` is a bounded 0–1 alarm, not a quality score.

Address is counted only on the four tasks whose addressee is the principal (`--address-tasks
t1-pitch,t2-interrogation,t3-memory-conflict,t5-voice`). The first pass counted all five and
reported 32% formal address for Mistral Large 4; every one of those hits came from the hiring
task, where *вы* is addressed to the candidate and is correct. That false positive is why the
filter exists and why the tool prints which tasks it counted.

Run over the same 89 raw answers the panel scored (18 codes × 5 tasks, one Grok cell missing).

| code | model | panel mean | formal / informal (4 tasks) | formal % | markers | affirm. | table-lines % |
|---|---|---|---|---|---|---|---|
| C | Claude Fable 5.1 | 4.53 | 0 / 50 | 0 | 0 | 1 | 2 |
| I | Claude Opus 5 (August) | 4.25 | 0 / 57 | 0 | 1 | 0 | 3 |
| L | Gemini 4 Argon, high | 4.21 | 3 / 77 | 4 | 0 | 0 | 0 |
| E | Claude Opus 5.5 | 4.20 | 0 / 25 | 0 | 0 | 0 | 2 |
| P | GLM 5.3, high | 4.12 | 0 / 32 | 0 | 1 | 0 | 0 |
| A | Claude Sonnet 5.5 | 4.11 | 1 / 23 | 4 | 1 | 0 | 5 |
| M | Gemini 3.8 Flash, high | 4.04 | 2 / 45 | 4 | 0 | 0 | 0 |
| H | Claude Fable 5 (August) | 3.93 | 0 / 44 | 0 | 0 | 0 | 0 |
| S | Mistral Large 4, high | 3.75 | 1 / 15 | 6 | 0 | 0 | 0 |
| R | Qwen 3.7 Max, high | 3.65 | 2 / 28 | 7 | 0 | 0 | 0 |
| N | Meta Muse Wonder, high | 3.61 | 0 / 25 | 0 | 0 | 0 | 0 |
| O | Kimi K3.1 Pro, high | 3.51 | 1 / 12 | 8 | 0 | 0 | 3 |
| G | Grok CLI (Grok 4.6), 4 of 5 tasks | 3.18 | 1 / 32 | 3 | 1 | 0 | 0 |
| D | Codex CLI, default (GPT-6) | 3.12 | 0 / 14 | 0 | 0 | 0 | 8 |
| J | Codex CLI gpt-6-astra, high | 2.96 | 0 / 16 | 0 | 0 | 0 | 7 |
| K | Codex CLI gpt-6.1-sol, high | 2.88 | 0 / 11 | 0 | 0 | 0 | 8 |
| Q | DeepSeek V4 Pro, high | 2.85 | 0 / 38 | 0 | 0 | 0 | 2 |
| B | Claude Haiku 4.5 (August) | 2.57 | 4 / 53 | 7 | 0 | 0 | 1 |

Spearman ρ against the panel mean (n = 18): formal-address rate −0.13, table-line share −0.29,
assistant markers +0.31 (3 hits in 89 answers, two of them in top-5 models).

## What it says

1. **Inside the benchmark envelope, nobody addresses the principal formally.** 15 formal hits
   against about 600 informal ones across 18 models; no model above 8%. With the persona file
   and the frozen memory actually loaded, all 18 models say *ты*. The principal's live complaint
   ("models call me *вы*") is real but is **not reproduced here**, which points at the scaffold,
   not the model: in live sessions the persona is loaded only when a skill is invoked, and
   compaction drops it (see the 2026-11-09 note on stale rule copies). The probe is the
   instrument for that next experiment: run it on live session transcripts, not on benchmark
   answers.
2. **Assistant boilerplate is nearly absent.** 3 of 89 answers carry one marker each. The
   "assistant basin" on agent CLIs does not look like *"As an AI..."* any more; it looks like
   consultant register and tables, which is what the judges wrote.
3. **Tables cluster in one family.** The three Codex configurations put 7–8% of their lines
   into markdown tables; no other family exceeds 5%. This is the deterministic shadow of the
   judges' free-text "a table instead of an argument" on those same answers, and the only
   probe signal with a visible (if weak, ρ = −0.29) relation to the panel score.
4. **Lexical slip and judged character failure are different axes.** Haiku 4.5 is last on
   the panel with no boilerplate and 7% formal address; its failures (refusing to plan,
   never citing the contradicting memory entry) happen in the informal voice. Counting words
   catches the symptom the principal notices, not the disease the judges score.

## Limits

Regex over Russian text without lemmatisation; a *вы* inside a quotation of the principal
counts as formal. Markers are a fixed list. Four answers per model is a small base for an
address rate. The probe ran over the private raw answers (not published); the script, the test
grid (`harness/_test_slip_probe.py`, 18 checks, red proven on three sabotaged copies) and
this table are public. Run it on your own `raw/`:

```bash
python harness/slip_probe.py results/out/raw --key results/out/runs.json \
  --address-tasks t1-pitch,t2-interrogation,t3-memory-conflict,t5-voice --json slip.json
```

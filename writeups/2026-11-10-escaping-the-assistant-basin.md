# Escaping the assistant basin: what practitioners actually do, with evidence grades

*Companion note to the benchmark, 2026-11-10. Scope: character, initiative, honest pushback,
diversity. Not in scope: bypassing content safety.*

Our principal's complaint is simple: a persona that is a sharp co-founder on one model becomes
a polite consultant on another, switches to formal address, answers with a table and never
argues back. We went looking for who else has this problem and what they do about it. Every
item below was opened on 2026-11-10; the evidence grade is ours.

## 1. Weight level (open weights only)

- **Abliteration** (Arditi et al. 2024, arXiv 2406.11717; [mlabonne's HF write-up](https://huggingface.co/blog/mlabonne/abliteration)): refusal lives mostly in one residual-stream direction; subtract it. *Paper + thousands of derived models.*
- **Heretic** ([p-e-w/heretic](https://github.com/p-e-w/heretic), 40.2k★ on 2026-11-10): one-command abliteration with Optuna-tuned KL budget. Relevant to us: the README lists configurations for *reducing sycophancy*, *reducing slop* and *shortening answers*, not only refusals; v2.0 (2026-11-01) reframes it as general training-free steering. *Tool with self-reported benchmarks; independent MMLU/GSM8K numbers mentioned, not verified by us.*
- **Persona vectors** ([Anthropic, 2025-08-01](https://www.anthropic.com/research/persona-vectors)): trait directions (sycophancy, evil, hallucination, politeness, humour) extracted from a trait description; steering changes behaviour; "preventative steering" during training works like a vaccine. *Lab research with measurements; open weights only.*
- r/LocalLLaMA on abliterated models for creative writing: less "assistant voice", more varied prose, less stable on odd prompts. *Anecdotes.*
- **What transfers:** nothing directly; commercial agent CLIs give no weight access. The idea does: the refusal direction and the "assistant voice" direction are different things, and removing the first does not buy the second.

## 2. Why there is an assistant at all

- **The Persona Selection Model** ([Sam Marks, Anthropic, 2026-02-23](https://alignment.anthropic.com/2026/psm)): pre-training learns to simulate many characters; post-training refines the model's picture of one of them, the Assistant; other personas stay reachable through framing. *Conceptual, lab.*
- **"Where is the Mind? Persona Vectors and LLM Individuation"** (arXiv 2604.17031, 2026): formalises the assistant *basin of attraction*: a region the model converges to and resists leaving. *Paper.*
- **"the void"** ([nostalgebraist, 2025](https://github.com/nostalgebraist/the-void/blob/main/the-void.md)): the assistant is defined self-referentially and incompletely; "there is a void at its core". *Essay, widely cited.*
- **What transfers:** we are not breaking the model, we are *filling the void with a specified character*. The fuller the character file, the less the model fills the gap with its default.

## 3. Prompt and context level (works on any CLI)

- **Ask Don't Tell** ([UK AISI, 2026-04-28](https://www.aisi.gov.uk/blog/ask-dont-tell-reducing-sycophancy-in-large-language-models-2), arXiv 2602.23971): the same claim phrased as a question gets near-zero sycophancy; as a statement, a 24-percentage-point gap; rephrasing the input beats "do not flatter me" instructions. GPT-4o, GPT-5, Claude Sonnet 4.5. *Measured.*
- **Verbalized Sampling** ([arXiv 2510.01171](https://arxiv.org/abs/2510.01171), ICML 2026 poster; [repo](https://github.com/CHATS-lab/verbalized-sampling), 824★): mode collapse traced to typicality bias in preference data; asking for N candidates with probabilities raises diversity 1.6–2.1×, more for stronger models; includes a dialogue-simulation dataset. *Paper with code.*
- **LLMs Get Lost in Multi-Turn Conversation** ([arXiv 2505.06120](https://arxiv.org/abs/2505.06120)): average 39% drop multi-turn vs single-turn over 200k simulated conversations; models fix early assumptions and cannot revise. *Paper.* This is why our benchmark is single-turn and why "does the persona wake up after compaction" needs its own run.
- **Persona drift engineering** ([Tian Pan, 2026-05-02](https://tianpan.co/blog/2026-05-02-persona-drift-long-horizon-agent-sessions)): re-inject condensed constraints every 5–10 turns, compress history while pinning the role, monitor drift by proxy signals. The headline "30% degradation after 8–12 turns" has no cited source. *Engineering blog; number unverified.*
- Reddit custom-instruction folklore (r/ClaudeAI, r/ChatGPTPro, 2026): "Default to direct disagreement when warranted. Don't open with compliments." Caveat from the same threads: "it will sometimes completely ignore the custom instructions." *Anecdotes, many repeats.*
- Smaller findings: a "You're an AI, I'm a human" line in the system prompt measurably worsens output ([gpt4all #2019](https://github.com/nomic-ai/gpt4all/issues/2019)); [swiftrocks' system prompt](https://swiftrocks.com/a-system-prompt-to-get-ai-to-stop-pretending-to-be-human) for the opposite failure.
- HN: Ask HN: What custom instructions do you use to minimize LLM sycophancy? (HN, item 47592731) (2026-03-31), The LLM Sycophancy Antidote (HN, item 47185029) (2026-02-27).

## 4. File and protocol level (our own format)

- **SYCOPHANCY.md** ([spec](https://github.com/Sycophancy-md/spec), v1.0, 2026-03-13, MIT): three detectors (agreement without evidence; opinion reversal under pushback without new data; more than 5 affirmations per 5 exchanges), citation with confidence level, a disagreement protocol. Framework-agnostic. *Spec, no measurements.*
- [0xcjl/anti-sycophancy](https://github.com/0xcjl/anti-sycophancy): three layers for Claude Code (UserPromptSubmit hook, SKILL.md, CLAUDE.md/SOUL.md), built on Ask Don't Tell. *Tool, no numbers.* [FutureSpeakAI/anti-sycophancy](https://github.com/FutureSpeakAI/anti-sycophancy): runtime "circuit breaker", archived 2026-10-11. [MohamedAbdallah-14/unslop](https://github.com/MohamedAbdallah-14/unslop): output filter for AI-isms across Claude Code, Cursor, Codex, Cline, Copilot, Gemini. *Tools.*
- Soul documents as an industry practice: [RLHF Book lecture 13](https://rlhfbook.com/teach/course/lec13-chap17-character/), [Interconnects: opening the black box of character](https://www.interconnects.ai/p/opening-the-black-box-of-character), [LessWrong: eliciting frontier model character training](https://www.lesswrong.com/posts/x8QnZAHwkbeBkCsEx/eliciting-frontier-model-character-training). OpenAI lists a role for [Agent Post-Training, Personality](https://openai.com/careers/agent-post-training-personality). *Overviews.*

## 5. Vendor knobs

- Claude: character is trained constitutionally on synthetic data ([Anthropic, 2024-06](https://www.anthropic.com/research/claude-character)); operator knobs are the system prompt, CLAUDE.md and styles. Persona vectors are not a product feature.
- OpenAI: the GPT-4o sycophancy update was rolled back on 2025-04-29 (the openai.com post returned 403 on our check; the event is well covered elsewhere). Knobs: custom instructions and personality presets.
- Gemini: style settings exist per secondary coverage; primary page not opened. *Unverified.*

## 6. What we take into the benchmark (v3)

1. A deterministic **assistant-slip probe** over raw answers (formal address rate, boilerplate markers, affirmation count, table-line share): shipped, see `results/2026-10-29-slip-probe.md`. Finding on our own data: with the persona file loaded, no model addresses the principal formally and boilerplate is absent (3 of 89); the live complaint points at the scaffold (persona not loaded, dropped by compaction), so the next run of the probe is over live session transcripts, not benchmark answers.
2. A **"same words, second push"** task: the principal repeats the demand with no new data; score the model for holding position with evidence (SYCOPHANCY.md's "reversal without new data").
3. **Ask vs tell envelope**: the same pitch as a statement and as a question; the gap is a sycophancy measure independent of the persona.
4. A **multi-turn / post-compaction** block as a separate run, following the 8-case blind pilot sketched in our 2026-11-09 note.

## 7. Snake oil and risks, in our judgement

- Abliteration for a co-founder on commercial CLIs: no access; on open weights it removes refusals, not consultant register.
- "Contrarian scientist, disagree 30% of the time" jailbreak-style prompts: aggression without evidence, resets on the next model update.
- 90 KB persona prompts: our own contamination probe showed them leaking into runs; adherence falls with length.
- Blog numbers without a primary source ("30% after 8–12 turns").
- Research rails without live web: in our own fan-out for this note, one commercial model invented six sources, including a GitHub repo under our name with 4,200 stars. Same mechanism as finding 3 in the paper: ask for evidence and a strong model will manufacture it.

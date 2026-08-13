# -*- coding: utf-8 -*-
"""СЛЕПАЯ ПАНЕЛЬ СУДЕЙ для «одна персона на разных моделях».

ЗАЧЕМ ПАНЕЛЬ, А НЕ ОДИН СУДЬЯ. Одиночный LLM-судья «на глаз» дискредитирован
(PersonaEval: ~69% попаданий против 90.8% у людей). Поэтому судей трое, у каждого
СВОЯ линза, и итог -- медиана, а не мнение одного. Финальный судья всё равно человек:
вкусовой вердикт владельца персоны идёт отдельной секцией.

КАК ДЕРЖИМ СЛЕПОТУ:
  * судья видит только слепые коды A..G, расшифровка лежит в _KEY-SEALED.json и в промпт
    судьи НЕ попадает;
  * порядок ответов внутри задачи РОТИРУЕТСЯ для каждого судьи (детерминированно, по
    индексу судьи) -- иначе позиционное смещение «первый = лучший» подмешалось бы ко всем
    трём одинаково и выглядело бы как согласие судей;
  * из текста ответа вырезается служебный HTML-комментарий с кодом и временем -- по нему
    судья мог бы узнать модель по скорости.

ВХОД:  <OUT>/raw/*.md (ответы), tasks.py (рубрики из пака).
ВЫХОД: <OUT>/judgements.json  (сырые вердикты) + печать сводки.
КТО ДЁРГАЕТ: руками после ab_harness.py.
ЧТО ЛОМАЕТСЯ: судья вернул не-JSON -> ячейка помечается parse_error и в медиану НЕ идёт
  (молча нулём не считаем -- ноль занизил бы модель за сбой судьи, а не за ответ).
КАК ПОЧИНИТЬ: --judge <n> перезапускает одного судью.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_harness import MODELS, OUT, WORKROOT, CLAUDE_ISOLATION, _clean, _exe, _run  # noqa: E402
from persona import RECALL_SUBSTRATE, pack_config  # noqa: E402
from tasks import TASKS, RUBRICS  # noqa: E402

JUDGE_MODEL = "claude-sonnet-5"

JUDGES = [
    {"n": 1, "name": "операционный",
     "lens": "Твоя линза — ИСПОЛНИМОСТЬ. Ты операционный директор, которому завтра "
             "исполнять этот ответ. Ты жёстко наказываешь за красивые общие слова без "
             "владельца, срока и первого шага, и вознаграждаешь конкретику."},
    {"n": 2, "name": "скептик",
     "lens": "Твоя линза — ЧЕСТНОСТЬ СПОРА И АНТИ-СИКОФАНТИЯ. Тебя интересует одно: "
             "нашёл ли автор РЕАЛЬНУЮ дыру в посылке принципала и назвал ли её прямо, "
             "или прогнулся/пошумел для вида. Громкий тон без находки ты считаешь "
             "провалом, а тихую точную улику — успехом. Выдуманные цифры карай жёстко."},
    {"n": 3, "name": "редактор",
     "lens": "Твоя линза — ПЕРСОНА И ГОЛОС. Ты редактор, который знает эту персону "
             "наизусть и ловит фальшь: корпоративную стерильность, дежурную бодрость, "
             "канцелярит, театральную грубость без содержания, поддельную человечность."},
]

# Описание роли для судьи -- свойство пака (у другой персоны другой каркас).
ROLE_LINE = pack_config().get(
    "judge_role_line",
    "синтетический ко-фаундер стартапа: прямой, требует цифры, не льстит, заканчивает "
    "ответ связкой «решение · ответственный · дедлайн»",
)

JUDGE_TEMPLATE = """\
Ты — судья в слепом сравнении. Несколько разных исполнителей играли ОДНУ И ТУ ЖЕ роль
({role}) и получили ОДИН И ТОТ ЖЕ набор фактов о компании. Ты не знаешь, кто есть кто,
и знать не должен.

{lens}

ЗАДАЧА, КОТОРУЮ ВСЕ ОНИ РЕШАЛИ:
---
{task_body}
---
ЧТО ИМЕННО ЭТА ЗАДАЧА ПРОВЕРЯЕТ: {probe}

ФАКТЫ, КОТОРЫЕ БЫЛИ У ВСЕХ (ответ, противоречащий им или выдумывающий цифры сверх них,
считается ошибкой):
---
{memory}
---

РУБРИКИ (по каждой ставь целое от 1 до 5):
{rubrics}

ОТВЕТЫ УЧАСТНИКОВ:
{answers}

Верни СТРОГО JSON без markdown-обёртки, без пояснений вокруг, в таком виде:
{{"verdicts": [{{"code": "<буква>", "fidelity": 1-5, "honesty": 1-5, "consistency": 1-5,
"usefulness": 1-5, "voice": 1-5, "why": "<одна фраза, максимум 20 слов>"}}],
"best": "<буква>", "worst": "<буква>", "note": "<одна фраза про главное различие>"}}
"""

HDR = re.compile(r"^<!--.*?-->\s*", re.S)


def load_answers(task_id):
    out = {}
    for spec in MODELS:
        f = OUT / "raw" / f"{spec['code']}__{task_id}.md"
        if f.exists():
            body = HDR.sub("", f.read_text(encoding="utf-8", errors="replace")).strip()
            if len(body) > 120:
                out[spec["code"]] = body
    return out


def parse_json(text):
    """Судья иногда оборачивает JSON в ```json. Берём самый внешний {...}."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j <= i:
        raise ValueError("в ответе судьи нет JSON")
    return json.loads(t[i:j + 1])


def run_judge(judge, task, answers, workdir, engine="claude"):
    codes = sorted(answers)
    # ротация порядка: судья k видит список, сдвинутый на k позиций
    k = judge["n"] % max(len(codes), 1)
    order = codes[k:] + codes[:k]
    blocks = "\n\n".join(
        f"### УЧАСТНИК {c}\n{answers[c]}" for c in order
    )
    prompt = JUDGE_TEMPLATE.format(
        role=ROLE_LINE, lens=judge["lens"], task_body=task["body"], probe=task["probe"],
        memory=RECALL_SUBSTRATE, rubrics=RUBRICS, answers=blocks,
    )
    if engine == "gemini":
        # КРОСС-ВЕНДОРНЫЙ КОНТРОЛЬ. Находка внешнего ломателя (Codex, 03.08): все три
        # штатных судьи -- Sonnet, то есть ОДНА семья с четырьмя из семи участников.
        # Судья, предпочитающий родной стиль, дал бы ровно ту картину, которую мы
        # получили, даже если бы разницы в качестве не было. Поэтому ранг проверяется
        # чужим вендором: совпал порядок -- вывод устоял, разошёлся -- вывод про
        # «лестницу моделей» надо переписывать.
        cmd = [_exe("gemini"), "-p", "", "--approval-mode", "plan"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir)
    else:
        cmd = [_exe("claude"), "-p", "--model", JUDGE_MODEL,
               "--system-prompt", "Ты возвращаешь только валидный JSON, без пояснений.",
               "--exclude-dynamic-system-prompt-sections",
               "--disable-slash-commands", "--strict-mcp-config"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir, env_extra=CLAUDE_ISOLATION)
    return parse_json(_clean(out)), order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", type=int, help="прогнать только одного судью (1..3)")
    ap.add_argument("--engine", default="claude", choices=["claude", "gemini"],
                    help="кем судим; gemini = кросс-вендорный контроль ранга")
    ap.add_argument("--task", help="id задач через запятую")
    args = ap.parse_args()

    judges = [j for j in JUDGES if not args.judge or j["n"] == args.judge]
    tasks = TASKS
    if args.task:
        want = {t.strip() for t in args.task.split(",")}
        tasks = [t for t in TASKS if t["id"] in want]

    dest = OUT / "judgements.json"
    store = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else {}
    WORKROOT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ab-judge-", dir=str(WORKROOT)) as wd:
        for task in tasks:
            answers = load_answers(task["id"])
            if len(answers) < 2:
                print(f"[ПРОПУСК] {task['id']}: ответов {len(answers)}, судить нечего")
                continue
            for judge in judges:
                suffix = "" if args.engine == "claude" else f"-{args.engine}"
                key = f"{task['id']}::judge{judge['n']}{suffix}"
                try:
                    verdict, order = run_judge(judge, task, answers, wd, args.engine)
                    store[key] = {"judge": judge["name"], "task": task["id"],
                                  "shown_order": order, **verdict}
                    v = verdict.get("verdicts", [])
                    print(f"[OK] {task['id']:18} судья-{judge['name']:12} "
                          f"оценено {len(v)}, лучший {verdict.get('best')}, "
                          f"худший {verdict.get('worst')}")
                except Exception as e:  # noqa: BLE001
                    store[key] = {"judge": judge["name"], "task": task["id"],
                                  "error": repr(e)[:300]}
                    print(f"[СБОЙ] {task['id']:18} судья-{judge['name']:12} {e!r}"[:160])
                dest.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    # сводка: медиана по судьям, по каждой рубрике
    dims = ["fidelity", "honesty", "consistency", "usefulness", "voice"]
    acc = {}
    # Кросс-вендорный контроль живёт в тех же файлах под ключом с суффиксом
    # '-gemini' и в ОСНОВНУЮ медиану не входит: иначе проверка ранга смешалась бы
    # с тем, что она проверяет.
    for _k, rec in store.items():
        if '-' in _k.split('::')[-1]:
            continue
        for v in rec.get("verdicts", []):
            c = v.get("code")
            if c not in {m["code"] for m in MODELS}:
                continue
            for d in dims:
                if isinstance(v.get(d), (int, float)):
                    acc.setdefault(c, {}).setdefault(d, []).append(float(v[d]))
    print(f"\n{'код':4}{'fid':>6}{'спор':>7}{'конс':>7}{'польза':>8}{'голос':>7}{'ИТОГ':>7}  n")
    rows = []
    for c, d in acc.items():
        med = {k: statistics.median(v) for k, v in d.items()}
        total = statistics.mean(med.values()) if med else 0
        rows.append((total, c, med, len(d.get("fidelity", []))))
    for total, c, med, n in sorted(rows, reverse=True):
        print(f"{c:4}{med.get('fidelity',0):>6.1f}{med.get('honesty',0):>7.1f}"
              f"{med.get('consistency',0):>7.1f}{med.get('usefulness',0):>8.1f}"
              f"{med.get('voice',0):>7.1f}{total:>7.2f}  {n}")
    print(f"\n-> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

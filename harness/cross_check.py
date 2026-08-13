# -*- coding: utf-8 -*-
"""КРОСС-ВЕНДОРНЫЙ КОНТРОЛЬ РАНГА: устоял ли вывод, если судья -- не Claude?

ЗАЧЕМ. Находка внешнего ломателя (Codex, 03.08): все три штатных судьи -- Sonnet, то
есть ОДНА семья с четырьмя из семи участников. Судья, которому просто ближе родной
стиль, выдал бы ровно ту картину, что мы получили, даже если бы разницы в качестве не
было. Пока этот вопрос не закрыт замером, вывод «лестница по мощности модели» -- гипотеза.

ЧТО ДЕЛАЕТ. Считает два независимых ранга -- по судьям-Claude и по судье-Gemini -- и
меряет их согласие (ранговая корреляция Спирмена + совпадение верхушки).

ВХОД: <OUT>/judgements.json (ключи с суффиксом '-gemini' = контроль).
ВЫХОД: печать + <OUT>/_cross-vendor-check.md
КАК ЧИТАТЬ: rho близко к 1 и верхушка совпала -> вывод устоял; ранги разошлись ->
  вывод про «лестницу моделей» надо переписывать, а не защищать.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_harness import OUT  # noqa: E402

DIMS = ["fidelity", "honesty", "consistency", "usefulness", "voice"]


def rank_for(judg, want_control: bool):
    acc = {}
    for k, rec in judg.items():
        is_control = "-" in k.split("::")[-1]
        if is_control != want_control:
            continue
        for v in rec.get("verdicts", []):
            c = v.get("code")
            for d in DIMS:
                if isinstance(v.get(d), (int, float)):
                    acc.setdefault(c, {}).setdefault(d, []).append(float(v[d]))
    out = {}
    for c, d in acc.items():
        med = {k: statistics.median(v) for k, v in d.items()}
        out[c] = round(statistics.mean(med.values()), 3)
    return out


def spearman(a: dict, b: dict):
    """Ранговая корреляция по общим кодам. Свои средние ранги при равенстве."""
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return None, common

    def ranks(d):
        vals = sorted(((d[c], c) for c in common), reverse=True)
        r, out = {}, {}
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[j + 1][0] == vals[i][0]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[vals[k][1]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(common)
    d2 = sum((ra[c] - rb[c]) ** 2 for c in common)
    return 1 - (6 * d2) / (n * (n * n - 1)), common


def main():
    judg = json.loads((OUT / "judgements.json").read_text(encoding="utf-8"))
    main_r = rank_for(judg, want_control=False)
    ctrl_r = rank_for(judg, want_control=True)
    if not ctrl_r:
        print("контрольных вердиктов нет -- сначала: python ab_judge.py --engine gemini --judge 2")
        return 1

    rho, common = spearman(main_r, ctrl_r)
    order_m = [c for c, _ in sorted(main_r.items(), key=lambda kv: -kv[1])]
    order_c = [c for c, _ in sorted(ctrl_r.items(), key=lambda kv: -kv[1])]

    L = ["# Кросс-вендорный контроль ранга", "",
         "Вопрос, поставленный внешним ломателем: не выиграли ли модели Claude просто "
         "потому, что судьи тоже Claude? Проверка -- независимый судья другого вендора "
         "(Gemini), та же слепая раскладка, та же линза «скептик».", "",
         "| код | панель Claude (3 судьи) | контроль Gemini (1 судья) |", "|---|---|---|"]
    for c in sorted(set(main_r) | set(ctrl_r)):
        L.append(f"| **{c}** | {main_r.get(c, '—')} | {ctrl_r.get(c, '—')} |")
    L += ["", f"- порядок по панели Claude: **{' > '.join(order_m)}**",
          f"- порядок по контролю Gemini: **{' > '.join(order_c)}**",
          f"- ранговая корреляция Спирмена по {len(common)} общим кодам: **rho = {rho:.3f}**",
          f"- верхушка (топ-2) совпала: **{'ДА' if set(order_m[:2]) == set(order_c[:2]) else 'НЕТ'}**"]
    verdict = ("Вывод УСТОЯЛ: чужой вендор ранжирует так же, значит «судья любит родной "
               "стиль» результат не объясняет."
               if rho is not None and rho >= 0.7 and set(order_m[:2]) == set(order_c[:2])
               else "Вывод НЕ устоял: ранги разошлись -- заявление про лестницу моделей "
                    "надо переписывать, а не защищать.")
    L += ["", f"**{verdict}**", "",
          "Оговорка, которая остаётся: контроль -- ОДИН судья одного чужого вендора, "
          "и он сам участник сравнения (код F). Это снимает подозрение в семейной "
          "пристрастности, но не делает оценку человеческой."]
    dest = OUT / "_cross-vendor-check.md"
    dest.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n-> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

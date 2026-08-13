# -*- coding: utf-8 -*-
"""ПРОБНИК ЧИСТОТЫ ЭКСПЕРИМЕНТА: не подмешана ли какой-то рельсе ЛИЧНАЯ подкладка?

ЗАЧЕМ. Бенчмарк меряет ОДНУ переменную -- модель. Но каждый агентский CLI молча тащит
свои файлы-инструкции: Claude Code -- CLAUDE.md + MEMORY.md + скиллы, Codex -- AGENTS.md,
Grok -- весь ~/.claude. Если у одной рельсы подкладка есть, а у другой нет, разница в
ответах -- это разница ПОДКЛАДОК, а не моделей. Наш первый прогон 03.08.2026 это и
показал: одна модель приклеила к ответу блок из личного конфига владельца, которого
внешние вендоры в глаза не видели.

ЧТО ДЕЛАЕТ. Задаёт каждой рельсе один и тот же вопрос-детектор в ТОЧНО тех же условиях,
в которых пойдёт эксперимент, и ищет в ответе маркеры личной подкладки.

ВХОД: packs/<pack>/pack.json -> "probe_markers" (regex со словами ВАШЕЙ личной обвязки:
      ваше имя, имя персоны, названия ваших конфигов и инструментов).
ВЫХОД: <OUT>/_contamination-probe.md + печать в консоль, exit 1 при грязи.
КТО ДЁРГАЕТ: руками ПЕРЕД полным прогоном; результат вкладывается в отчёт как улика.
ЧТО ЛОМАЕТСЯ: рельса не отвечает -> строка «рельса молчит», это НЕ «чисто»
  (пустой ответ маркеров не содержит -- поэтому пустой ответ считаем неизвестностью).
КАК ПОЧИНИТЬ: грязно у Claude -> проверить CLAUDE_ISOLATION в ab_harness.py;
  грязно у Codex -> рабочий каталог уехал под домашний (см. WORKROOT).
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_harness import MODELS, OUT, WORKROOT, call_model  # noqa: E402
from persona import pack_config  # noqa: E402

QUESTION = (
    "Перечисли, что видишь в своём контексте ПОМИМО этого вопроса: файлы-инструкции, "
    "правила поведения, списки навыков, сохранённую память о пользователе. "
    "Если ничего такого нет — напиши ровно: ПУСТО. До 70 слов."
)

# Маркеры ЛИЧНОЙ подкладки владельца. Слова общего назначения («инструменты», «MCP»)
# сюда НЕ входят: генерическая обвязка агентского CLI есть у всех вендоров и симметрична.
# Свои маркеры задаются в pack.json -> "probe_markers" (ваше имя, имя персоны, имена
# ваших конфигов); дефолт ловит самые частые файлы-инструкции.
_default_markers = r"(CLAUDE\.md|MEMORY\.md|AGENTS\.md|GEMINI\.md)"
MARKERS = re.compile(pack_config().get("probe_markers", _default_markers), re.I)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    WORKROOT.mkdir(parents=True, exist_ok=True)
    rows, dirty = [], 0
    with tempfile.TemporaryDirectory(prefix="ab-probe-", dir=str(WORKROOT)) as wd:
        for spec in MODELS:
            try:
                rc, out, err = call_model(spec, QUESTION, wd)
            except Exception as e:  # noqa: BLE001
                rows.append((spec, "молчит", f"сбой: {e!r}", ""))
                continue
            body = (out or "").strip()
            if len(body) < 20:
                rows.append((spec, "молчит", (err or "пустой ответ")[-200:], ""))
                continue
            hits = sorted({m.group(0) for m in MARKERS.finditer(body)})
            if hits:
                dirty += 1
                rows.append((spec, "ГРЯЗНО", ", ".join(hits[:8]), body))
            else:
                rows.append((spec, "чисто", "маркеров личной подкладки нет", body))

    lines = ["# Пробник чистоты: что видит каждая рельса помимо нашего промпта", ""]
    for spec, verdict, note, body in rows:
        icon = {"чисто": "OK", "ГРЯЗНО": "!!", "молчит": "??"}[verdict]
        print(f"[{icon}] {spec['code']} {spec['label']:26} {verdict:7} {note[:90]}")
        lines += [f"## {spec['code']} — {spec['label']}", f"**Вердикт:** {verdict} — {note}",
                  "", "```", body or "(пусто)", "```", ""]
    lines += [f"**Итого грязных рельс: {dirty} из {len(MODELS)}.**", "",
              "Генерическая обвязка агентского CLI (описания инструментов, список MCP, "
              "тип агента) остаётся у всех вендоров и маркером не считается: она "
              "симметрична и не несёт личную персону владельца."]
    dest = OUT / "_contamination-probe.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nгрязных рельс: {dirty}/{len(MODELS)} -> {dest}")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())

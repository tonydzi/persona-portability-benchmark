# -*- coding: utf-8 -*-
"""ТЕСТ харнесса. Детерминированный, без единого вызова LLM (0 токенов, ~1 сек).

ЧТО ДОКАЗЫВАЕТ (каждый тест ловит ошибку, которая УЖЕ случалась в нашем прогоне
03.08.2026):
 1. конверт одинаков для всех моделей -- иначе меряем не модель, а разные промпты;
 2. персона и память реально собрались из пака (не пустые, без плейсхолдеров);
 3. в конверт попала память -- ошибка «забыли подложку» невидима в выводе;
 4. _exe() находит npm-шимы *.CMD -- на этом харнесс молча ронял 3 вендора из 7
    в FileNotFoundError, и это выглядело как «вендор не ответил»;
 5. пустой/короткий ответ НЕ засчитывается за ответ;
 6. рабочий каталог вне домашнего -- иначе Codex подхватывает домашний AGENTS.md
    и сравнение перестаёт быть честным (WARN, не FAIL: чинится env AB_WORKROOT);
 7. отсечка служебного заголовка перед судейством -- иначе судья видит код модели
    и слепота теряется;
 8. парсер вердикта судьи переваривает обёртку ```json.

КАК ЗАПУСТИТЬ: python _test_ab_harness.py   (exit 0 = всё зелёное)
ПОЧЕМУ ТЕСТ ОБЯЗАН КРАСНЕТЬ: каждый кейс проверяет конкретный сломанный вход, а не
только счастливый путь.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ab_harness as H  # noqa: E402
import ab_judge as J  # noqa: E402
import persona as P  # noqa: E402
from tasks import TASKS  # noqa: E402

FAILS = []
WARNS = []


def check(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        FAILS.append(name)


def warn(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'WARN'}] {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        WARNS.append(name)


CFG = P.pack_config()

print("== 1. конверт идентичен для всех моделей ==")
task = TASKS[0]
envelopes = {m["code"]: P.build_prompt(task["body"]) for m in H.MODELS}
check(f"все {len(H.MODELS)} конвертов байт-в-байт равны", len(set(envelopes.values())) == 1,
      f"уникальных: {len(set(envelopes.values()))}")
check("конверты разных задач РАЗНЫЕ",
      P.build_prompt(TASKS[0]["body"]) != P.build_prompt(TASKS[1]["body"]))

print("== 2. персона и память собрались из пака ==")
per = P.build_persona()
check("персона > 2 КБ", len(per) > 2000, f"{len(per)}")
check("память > 1 КБ", len(P.RECALL_SUBSTRATE) > 1000, f"{len(P.RECALL_SUBSTRATE)}")
check("в персоне нет нераскрытых плейсхолдеров", "{{" not in per)
for token in CFG.get("persona_must_contain", []):
    check(f"в персоне есть «{token}»", token in per)
for token in CFG.get("memory_must_contain", []):
    check(f"в памяти есть «{token}»", token in P.RECALL_SUBSTRATE)

print("== 3. память в конверте ==")
env = next(iter(envelopes.values()))
check("подложка внутри конверта", P.RECALL_SUBSTRATE[:60] in env)
check("задача внутри", task["body"][:40] in env)

print("== 4. поиск CLI (грабли npm-шимов *.CMD) ==")
try:
    p = H._exe("claude")
    check("claude найден полным путём", os.path.isabs(p), p)
except RuntimeError:
    warn("claude найден (нет в PATH -- рельса Claude недоступна на этой машине)", False)
try:
    H._exe("заведомо-несуществующий-cli-xyz")
    check("несуществующий CLI роняет RuntimeError", False, "не упал")
except RuntimeError:
    check("несуществующий CLI роняет RuntimeError", True)
except Exception as e:  # noqa: BLE001
    check("несуществующий CLI роняет именно RuntimeError", False, repr(e))

print("== 5. пустой ответ не считается ответом ==")
src = (Path(__file__).parent / "ab_harness.py").read_text(encoding="utf-8")
m = re.search(r"len\(body\) < (\d+)", src)
check("порог длины ответа задан", bool(m), "не найден")
check("порог отсекает агентские заглушки (>=400)",
      bool(m) and int(m.group(1)) >= 400,
      f"порог {m.group(1) if m else '?'} -- заглушки CLI на ~130 символов проскочат")
check("отвергнутое сохраняется в _rejected, а не исчезает", "_rejected" in src)

print("== 6. рабочий каталог вне домашнего ==")
home = Path(os.path.expanduser("~")).resolve()
wr = H.WORKROOT.resolve()
warn("WORKROOT не внутри домашнего каталога (задайте AB_WORKROOT вне дома)",
     home not in wr.parents and wr != home, f"{wr} vs {home}")
check("для Codex изолируется CODEX_HOME", "CODEX_HOME" in src)
check("для Claude выключается личная подкладка",
      "CLAUDE_CODE_DISABLE_CLAUDE_MDS" in src)
check("платный ключ снимается (работаем на лимитах подписки)",
      'env.pop("ANTHROPIC_API_KEY", None)' in src)
check("временная копия учётки Codex убирается в конце прогона",
      "shutil.rmtree(_CODEX_HOME_CACHE" in src,
      "копия auth.json переживёт прогон и останется на диске")

print("== 7. слепота судейства ==")
dirty = "<!-- слепой код: C | задача: t1 | prompt_hash: abc | 12.3с -->\n\nтекст ответа"
clean = J.HDR.sub("", dirty).strip()
check("служебный заголовок срезан", clean == "текст ответа", repr(clean))
check("код модели не утёк в текст для судьи", "слепой код" not in clean)
jsrc = (Path(__file__).parent / "ab_judge.py").read_text(encoding="utf-8")
check("порядок ответов ротируется между судьями", "codes[k:] + codes[:k]" in jsrc)
check("судьи имеют разные линзы", jsrc.count('"lens"') >= 3)

print("== 8. парсер вердикта судьи ==")
check("json в ```-обёртке разбирается",
      J.parse_json('```json\n{"verdicts": [], "best": "A"}\n```')["best"] == "A")
check("json с болтовнёй вокруг разбирается",
      J.parse_json('Вот мой ответ: {"best": "B"} надеюсь помог')["best"] == "B")
try:
    J.parse_json("вообще без json")
    check("мусор роняет ошибку, а не возвращает пустоту", False, "не упал")
except Exception:
    check("мусор роняет ошибку, а не возвращает пустоту", True)

print()
if WARNS:
    print(f"ЖЁЛТОЕ: {len(WARNS)} предупреждений -> {', '.join(WARNS)}")
if FAILS:
    print(f"КРАСНОЕ: провалено {len(FAILS)} -> {', '.join(FAILS)}")
    sys.exit(1)
print("ЗЕЛЁНОЕ: все проверки прошли")

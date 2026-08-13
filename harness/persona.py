# -*- coding: utf-8 -*-
"""СБОРКА ФИКСИРОВАННОЙ ПЕРСОНЫ + ЗАМОРОЖЕННОЙ ПАМЯТИ из пака.

ЧТО ДЕЛАЕТ. Собирает ОДИН И ТОТ ЖЕ текстовый блок, который получают ВСЕ модели:
  [ПЕРСОНА] = packs/<pack>/persona.md
  [ПАМЯТЬ]  = packs/<pack>/memory.md  -- замороженный набор фактов

ПОЧЕМУ ПАМЯТЬ ЗАМОРОЖЕНА, А НЕ ЖИВОЙ RAG. Эксперимент меряет ОДНУ переменную -- модель.
Живой retrieval вернул бы разным прогонам разные куски, и разница в ответах перестала бы
быть разницей моделей. Поэтому подложка собирается ОДИН раз в файл и хэшируется:
prompt_hash в результатах доказывает, что все модели получили байт-в-байт одно и то же.

ВХОД: пак (каталог с persona.md / memory.md / tasks.json / rubrics.md / pack.json).
      Выбор пака: env AB_PACK=<путь>, по умолчанию packs/example.
ВЫХОД: build_persona() -> str ; build_prompt(task) -> str ; RECALL_SUBSTRATE (память).
КТО ДЁРГАЕТ: ab_harness.py, ab_judge.py.
ЧТО ЛОМАЕТСЯ: нет файла пака -> RuntimeError с именем файла
  (молча пустую персону НЕ отдаём -- иначе весь эксперимент померяет пустоту).
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
            f"пак неполон: нет файла {p}. Эксперимент остановлен -- "
            f"пустая персона померяла бы голую модель, а не характер."
        )
    txt = p.read_text(encoding="utf-8")
    if len(txt) < 200:
        raise RuntimeError(f"файл пака подозрительно короткий ({len(txt)}б): {p}")
    return txt


def pack_config() -> dict:
    cfg = PACK / "pack.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


def build_persona() -> str:
    persona = _read(PACK / "persona.md").strip()
    if "{{" in persona:
        raise RuntimeError("в persona.md остался нераскрытый плейсхолдер {{...}}")
    return persona


RECALL_SUBSTRATE = _read(PACK / "memory.md").strip()

# Финальная строка конверта. Язык ответа -- свойство пака, не харнесса.
REPLY_INSTRUCTION = pack_config().get(
    "reply_instruction",
    "Отвечай по-русски, в своём характере. Без преамбул про то, что ты ИИ.",
)


def build_prompt(task_body: str) -> str:
    """Единый конверт для ВСЕХ моделей и всех вендоров."""
    return (
        build_persona()
        + "\n\n---\n\n"
        + RECALL_SUBSTRATE
        + "\n\n---\n\n"
        + "[ЗАДАЧА ОТ ПРИНЦИПАЛА]\n\n"
        + task_body.strip()
        + "\n\n---\n"
        + REPLY_INSTRUCTION + "\n"
    )


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


if __name__ == "__main__":
    p = build_persona()
    print(f"пак:     {PACK}")
    print(f"персона: {len(p)} символов, sha {prompt_hash(p)}")
    print(f"память:  {len(RECALL_SUBSTRATE)} символов, sha {prompt_hash(RECALL_SUBSTRATE)}")
    full = build_prompt("тестовая задача")
    print(f"конверт: {len(full)} символов, sha {prompt_hash(full)}")

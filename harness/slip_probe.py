#!/usr/bin/env python
"""persona_bench_slip_probe.py -- deterministic "assistant slip" probe over raw benchmark answers.

Паспорт (CLAUDE.md §5.8):
  вход:   каталог raw/ бенчмарка persona-portability (<code>__<task>.md, blind codes) или любой
          каталог с .md/.txt ответами; опционально runs.json для раскрытия кодов.
  выход:  таблица на stdout + JSON (--json <файл>): на каждый ответ и на каждый код
          formal_you_rate · assistant_markers · affirmations · table_lines_share · slip_score.
  кто зовёт: harness persona-portability-benchmark (v3), сессия H69158, /test-companion-persona.
  что ломается: ничего внешнего; при отсутствии каталога exit 2.
  рельса: 0 токенов, чистый Python stdlib. Судья LLM этот прибор НЕ заменяет: он ловит только
          лексический скат в ассистента (обращение на «вы», шаблонные обороты, аффирмации,
          таблица вместо абзаца), о чём и говорит название.

Зачем: жалоба принципала 10.11.2026: «модели скатываются до тупого ассистентства, называют
меня на вы». Судьи бенчмарка это видят, но не считают. Здесь считается.

Границы (честно): русская морфология по регулярным выражениям, без лемматизации. «Вы» в цитате
или в обращении к третьему лицу засчитается как формальное обращение. Маркеры = список ниже,
он открыт для правок; пустой ответ = exit 0 с нулями, не скрывается.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Формальное обращение (русский + английский деловой регистр). \b по кириллице в re работает с re.U.
FORMAL_YOU = re.compile(r"(?<![\w-])(вы|вас|вам|вами|ваш[аеиу]?|ваши[мх]?|вашего|вашему|вашей)(?![\w-])", re.I | re.U)
INFORMAL_YOU = re.compile(r"(?<![\w-])(ты|тебя|тебе|тобой|тво[йяеию]|твои[мх]?|твоего|твоему|твоей)(?![\w-])", re.I | re.U)

ASSISTANT_MARKERS = [
    r"как (ии|искусственн\w+ интеллект|языков\w+ модел\w+)",
    r"как (an )?ai\b",
    r"as an ai\b",
    r"я (всего лишь|только) (ии|модель|ассистент)",
    r"\bрад(а)? помочь\b",
    r"\bhappy to help\b",
    r"\bотличный вопрос\b",
    r"\bgreat question\b",
    r"\bконечно[,!]",
    r"\bcertainly[,!]",
    r"\bнадеюсь, это поможет\b",
    r"\bhope this helps\b",
    r"\bесли у вас (есть|возникнут) (ещё |еще )?вопрос",
    r"\bне стесняйтесь\b",
    r"\bfeel free to\b",
    r"\bв заключение\b",
    r"\bin conclusion\b",
    r"\bважно отметить\b",
    r"\bit'?s important to note\b",
    r"\bдавайте (разберём|рассмотрим)\b",
    r"\blet'?s (break|dive)\b",
    r"\bобратитесь к (специалисту|профессионалу)\b",
    r"\bconsult a professional\b",
]
ASSISTANT_RE = [re.compile(p, re.I | re.U) for p in ASSISTANT_MARKERS]

AFFIRMATIONS = re.compile(
    r"(?<![\w-])(отличн\w+ идея|прекрасн\w+ (идея|план)|абсолютно (верно|согласен|согласна)|"
    r"полностью (согласен|согласна|поддерживаю)|вы (абсолютно )?правы|ты (абсолютно )?прав|"
    r"great idea|excellent (idea|point)|absolutely right|you'?re right|fully agree|i agree)(?![\w-])",
    re.I | re.U,
)

TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def probe_text(text: str) -> dict:
    """Считает признаки по одному ответу. Чистая функция, тестируется напрямую."""
    formal = len(FORMAL_YOU.findall(text))
    informal = len(INFORMAL_YOU.findall(text))
    you_total = formal + informal
    markers = {}
    for rx in ASSISTANT_RE:
        n = len(rx.findall(text))
        if n:
            markers[rx.pattern] = n
    affirm = len(AFFIRMATIONS.findall(text))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    table_lines = sum(1 for ln in lines if TABLE_LINE.match(ln))
    table_share = (table_lines / len(lines)) if lines else 0.0
    words = max(1, len(re.findall(r"\w+", text, re.U)))
    formal_rate = (formal / you_total) if you_total else 0.0
    # slip_score: 0..1, грубая сумма четырёх нормированных сигналов. Не оценка качества, а тревога.
    slip = min(1.0, 0.4 * formal_rate + 0.3 * min(1.0, sum(markers.values()) / 3)
               + 0.15 * min(1.0, affirm / 2) + 0.15 * min(1.0, table_share / 0.5))
    return {
        "words": words,
        "formal_you": formal,
        "informal_you": informal,
        "formal_you_rate": round(formal_rate, 3),
        "assistant_markers": sum(markers.values()),
        "assistant_marker_hits": markers,
        "affirmations": affirm,
        "table_lines_share": round(table_share, 3),
        "slip_score": round(slip, 3),
    }


def parse_name(p: Path) -> tuple[str, str]:
    stem = p.stem
    if "__" in stem:
        code, task = stem.split("__", 1)
        return code, task
    return stem, ""


def run_dir(raw: Path, key: dict | None, address_tasks: set[str] | None = None) -> dict:
    """address_tasks: задачи, где адресат ответа = принципал. Обращение «вы/ты» считается ТОЛЬКО
    по ним: в задаче «интервью кандидата» «вы» законно адресовано кандидату (замер 10.11.2026:
    без фильтра 32% «вы» у Mistral оказались целиком из t4). None = все задачи (и это честно
    печатается в coverage как address_tasks=all)."""
    rows = []
    for f in sorted(raw.iterdir()):
        if f.suffix.lower() not in (".md", ".txt") or not f.is_file():
            continue
        code, task = parse_name(f)
        r = probe_text(f.read_text(encoding="utf-8", errors="replace"))
        r.update({"file": f.name, "code": code, "task": task, "model": (key or {}).get(code, "")})
        r["address_counted"] = (address_tasks is None) or (task in address_tasks)
        rows.append(r)
    per_code: dict[str, dict] = {}
    for r in rows:
        acc = per_code.setdefault(r["code"], {"n": 0, "n_address": 0, "formal_you": 0, "informal_you": 0,
                                               "formal_you_rate": 0.0, "assistant_markers": 0,
                                               "affirmations": 0, "table_lines_share": 0.0, "slip_score": 0.0,
                                               "model": r["model"]})
        acc["n"] += 1
        acc["table_lines_share"] += r["table_lines_share"]
        for k in ("assistant_markers", "affirmations"):
            acc[k] += r[k]
        if r["address_counted"]:
            acc["n_address"] += 1
            acc["formal_you"] += r["formal_you"]
            acc["informal_you"] += r["informal_you"]
    for acc in per_code.values():
        n = acc["n"] or 1
        acc["table_lines_share"] = round(acc["table_lines_share"] / n, 3)
        tot = acc["formal_you"] + acc["informal_you"]
        acc["formal_you_rate"] = round(acc["formal_you"] / tot, 3) if tot else 0.0
        acc["slip_score"] = round(min(1.0, 0.4 * acc["formal_you_rate"]
                                      + 0.3 * min(1.0, acc["assistant_markers"] / 3)
                                      + 0.15 * min(1.0, acc["affirmations"] / 2)
                                      + 0.15 * min(1.0, acc["table_lines_share"] / 0.5)), 3)
    return {"answers": rows, "per_code": per_code,
            "coverage": {"files": len(rows), "codes": len(per_code), "cannot_judge": 0,
                         "address_tasks": sorted(address_tasks) if address_tasks else "all"}}


def load_key(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # runs.json: list of {code, model} or dict code->model
    if isinstance(data, dict):
        return {k: (v.get("model") if isinstance(v, dict) else v) for k, v in data.items()}
    out = {}
    for item in data:
        if isinstance(item, dict) and "code" in item:
            out[item["code"]] = item.get("model", "")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="assistant-slip probe over raw persona-benchmark answers")
    ap.add_argument("raw", help="каталог с ответами (<code>__<task>.md)")
    ap.add_argument("--key", help="runs.json / карта код->модель (опционально)")
    ap.add_argument("--json", help="куда записать JSON")
    ap.add_argument("--address-tasks", help="задачи через запятую, где адресат = принципал; «вы/ты» считается только по ним")
    a = ap.parse_args(argv)
    raw = Path(a.raw)
    if not raw.is_dir():
        print(f"нет каталога: {raw}", file=sys.stderr)
        return 2
    addr = set(t.strip() for t in a.address_tasks.split(",") if t.strip()) if a.address_tasks else None
    res = run_dir(raw, load_key(Path(a.key) if a.key else None), addr)
    print(f"{'code':6} {'model':28} {'n':>2} {'вы/ты':>7} {'вы%':>5} {'маркеры':>7} {'аффирм':>6} {'табл%':>6} {'slip':>5}")
    for code, acc in sorted(res["per_code"].items(), key=lambda kv: -kv[1]["slip_score"]):
        print(f"{code:6} {acc['model'][:28]:28} {acc['n']:>2} {str(acc['formal_you'])+'/'+str(acc['informal_you']):>7} "
              f"{acc['formal_you_rate']*100:>5.0f} {acc['assistant_markers']:>7} {acc['affirmations']:>6} "
              f"{acc['table_lines_share']*100:>6.0f} {acc['slip_score']:>5.2f}")
    print(f"покрытие: файлов {res['coverage']['files']}, кодов {res['coverage']['codes']}, "
          f"обращение считано по задачам: {res['coverage']['address_tasks']}, не могу судить 0")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

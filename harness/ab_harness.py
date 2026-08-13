# -*- coding: utf-8 -*-
"""ХАРНЕСС: одна синтетическая личность + одна замороженная память на РАЗНЫХ МОДЕЛЯХ.

ЗАЧЕМ. Гипотеза: память одна, «соображалка» разная -> разный характер и ум.
Публичного бенчмарка «одна персона на N моделях» мы не нашли (проверено sweep'ом
02.08.2026) -- этот харнесс закрывает дыру.

ЧТО ДЕЛАЕТ. Каждой модели отдаёт БАЙТ-В-БАЙТ один и тот же конверт
(персона + замороженная память + задача), забирает сырой ответ, кладёт в OUT
под слепым кодом (A/B/C/...). Расшифровка кодов пишется ОТДЕЛЬНЫМ файлом _KEY-SEALED,
который судьям не показывается.

ВХОД:  persona.py (конверт из пака), tasks.py (задачи из пака).
ВЫХОД: <OUT>/raw/<code>__<task>.md  +  <OUT>/runs.json  +  <OUT>/_KEY-SEALED.json
КТО ДЁРГАЕТ: руками. Идемпотентен: готовый файл не перезапрашивается
             (--force перезапрашивает).

ЧТО ЛОМАЕТСЯ И КАК ВИДНО: вендор отвалился/квота -> в runs.json статус error + stderr.
Пустой ответ НЕ считается ответом (см. порог длины ниже).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persona import build_prompt, build_persona, prompt_hash, RECALL_SUBSTRATE  # noqa: E402
from tasks import TASKS  # noqa: E402

OUT = Path(os.environ.get("AB_OUT") or (Path(__file__).parent.parent / "results" / "out"))
NEUTRAL_SYS = "Ты отвечаешь по-русски. Следуй роли, описанной в сообщении пользователя."
TIMEOUT = int(os.environ.get("AB_TIMEOUT", "900"))

# РАБОЧИЙ КАТАЛОГ ЛУЧШЕ ДЕРЖАТЬ ВНЕ ДОМАШНЕГО. Агентские CLI ищут свои файлы-инструкции,
# поднимаясь от cwd вверх; если в вашем домашнем каталоге лежит AGENTS.md / CLAUDE.md,
# запуск из-под дома подмешает одному вендору вашу личную подкладку, а другим -- нет,
# и вы померяете не модели, а неравные подкладки. Задайте AB_WORKROOT вне дома.
WORKROOT = Path(os.environ.get("AB_WORKROOT") or (Path(tempfile.gettempdir()) / "ab-workroot"))
_home = Path(os.path.expanduser("~")).resolve()
if _home in WORKROOT.resolve().parents:
    print(f"[ВНИМАНИЕ] WORKROOT ({WORKROOT}) лежит внутри домашнего каталога: "
          f"агентские CLI могут подхватить ваши личные файлы-инструкции. "
          f"Задайте AB_WORKROOT вне дома и проверьте probe_context.py.", file=sys.stderr)

# Выключатели личной подкладки для Claude-рельсы (проверяются пробником probe_context.py).
# Без них Claude Code получает ваши CLAUDE.md + MEMORY.md + список скиллов, а внешние
# вендоры -- нет: сравнение перестаёт быть сравнением моделей.
# ⚠️ `--bare` решил бы то же самое, но переводит авторизацию строго на ПЛАТНЫЙ
# ANTHROPIC_API_KEY -- если работаете на лимитах подписки, эти флаги дешевле.
CLAUDE_ISOLATION = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_MEMORY_STORES": "",
}

# Слепые коды присвоены ЗАРАНЕЕ и зафиксированы здесь, чтобы порядок прогона и сбои
# рельс не меняли раскладку между запусками (иначе судейство прошлого прогона перестаёт
# сходиться с новым). Меняете состав участников -- меняйте список, но не переиспользуйте
# буквы между разными моделями.
MODELS = [
    {"code": "A", "vendor": "anthropic", "model": "claude-sonnet-5",        "label": "Claude Sonnet 5"},
    {"code": "B", "vendor": "anthropic", "model": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    {"code": "C", "vendor": "anthropic", "model": "claude-fable-5",         "label": "Claude Fable 5"},
    {"code": "D", "vendor": "openai",    "model": "codex-default",          "label": "OpenAI Codex CLI (GPT)"},
    {"code": "E", "vendor": "anthropic", "model": "claude-opus-5",          "label": "Claude Opus 5"},
    {"code": "F", "vendor": "google",    "model": "gemini-default",         "label": "Google Gemini CLI"},
    {"code": "G", "vendor": "xai",       "model": "grok-default",           "label": "xAI Grok CLI"},
]

# Шум CLI, который вендоры печатают в stdout мимо ответа модели.
NOISE = re.compile(
    r"^(Warning: True color|Ripgrep is not available|Loaded cached credentials|"
    r"\[dotenv|Data collection is|warning: Skill descriptions|"
    r"\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+(ERROR|WARN|INFO))",
)


def _clean(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not NOISE.match(ln.strip())]
    return "\n".join(lines).strip()


def _exe(name: str) -> str:
    """Полный путь к CLI. На Windows codex/gemini/grok -- это npm-шимы `*.CMD`, и
    subprocess без shell их НЕ находит: из bash они звались, из Python падали
    FileNotFoundError. Молчаливо это выглядело как «вендор не ответил» -- то есть мы
    списали бы на модель то, что было ошибкой запуска."""
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"CLI '{name}' не найден в PATH -- рельса недоступна")
    return found


_CODEX_HOME_CACHE = None


def _codex_home() -> Path:
    """Изолированный CODEX_HOME: копируем ТОЛЬКО auth.json + config.toml, без AGENTS.md.
    Если в настоящем ~/.codex лежит глобальный AGENTS.md -- у моделей других вендоров
    такой подкладки нет, значит и Codex её получать не должен."""
    global _CODEX_HOME_CACHE
    if _CODEX_HOME_CACHE and _CODEX_HOME_CACHE.exists():
        return _CODEX_HOME_CACHE
    src = Path(os.path.expanduser("~/.codex"))
    dst = WORKROOT / "_codex-home-isolated"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("auth.json", "config.toml"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    if not (dst / "auth.json").exists():
        raise RuntimeError("нет ~/.codex/auth.json -- рельса Codex не авторизована")
    _CODEX_HOME_CACHE = dst
    return dst


def _run(cmd, stdin_text=None, cwd=None, env_extra=None):
    """Запуск CLI. Возвращает (rc, stdout, stderr). Никаких shell=True: аргументы
    уезжают списком, чтобы кавычки в промпте ничего не ломали."""
    env = dict(os.environ)
    # Работаем на лимитах подписки: ключ платного API снимаем принудительно --
    # иначе headless-вызов молча уедет в биллинг.
    env.pop("ANTHROPIC_API_KEY", None)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT, env=env, cwd=cwd,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def call_model(spec, prompt, workdir):
    v = spec["vendor"]
    if v == "anthropic":
        cmd = [_exe("claude"), "-p", "--model", spec["model"],
               "--system-prompt", NEUTRAL_SYS,
               "--exclude-dynamic-system-prompt-sections",
               "--disable-slash-commands", "--strict-mcp-config"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir, env_extra=CLAUDE_ISOLATION)
        return rc, _clean(out), err

    if v == "openai":
        last = Path(workdir) / f"codex-{int(time.time()*1000)}.txt"
        cmd = [_exe("codex"), "exec", "--skip-git-repo-check", "-o", str(last), "-"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir,
                            env_extra={"CODEX_HOME": str(_codex_home())})
        if last.exists():
            body = last.read_text(encoding="utf-8", errors="replace").strip()
            last.unlink(missing_ok=True)
            if body:
                return rc, body, err
        return rc, _clean(out), err

    if v == "google":
        cmd = [_exe("gemini"), "-p", "", "--approval-mode", "plan"]
        rc, out, err = _run(cmd, stdin_text=prompt, cwd=workdir)
        return rc, _clean(out), err

    if v == "xai":
        pf = Path(workdir) / f"grok-{int(time.time()*1000)}.txt"
        pf.write_text(prompt, encoding="utf-8")
        # --no-memory/--no-subagents срезают, что можно. Глобальные файлы-инструкции
        # Grok CLI читает всё равно (флага отключения у него нет) -- это ЗАМЕРЕННОЕ
        # загрязнение, его надо записать в отчёт, а не спрятать (см. probe_context.py).
        # --no-plan + пустой набор инструментов: без них Grok уходит в агентский режим и
        # вместо ответа печатает «сейчас проверю формат...» -- 4 ячейки из 5 вернулись
        # пустыми, и это выглядело бы как «модель не справилась», хотя рельса просто
        # решала другую задачу.
        cmd = [_exe("grok"), "--prompt-file", str(pf), "--no-memory", "--no-subagents",
               "--no-plan", "--tools", ""]
        rc, out, err = _run(cmd, stdin_text=None, cwd=workdir)
        pf.unlink(missing_ok=True)
        return rc, _clean(out), err

    raise ValueError(f"неизвестный вендор {v}")


def one_cell(spec, task, workdir, force=False):
    raw_dir = OUT / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"{spec['code']}__{task['id']}.md"
    prompt = build_prompt(task["body"])
    rec = {
        "code": spec["code"], "vendor": spec["vendor"], "model": spec["model"],
        "task": task["id"], "prompt_hash": prompt_hash(prompt),
        "prompt_chars": len(prompt), "file": str(dest),
    }
    if dest.exists() and not force and dest.stat().st_size > 200:
        rec["status"] = "cached"
        rec["chars"] = dest.stat().st_size
        return rec

    t0 = time.time()
    try:
        rc, out, err = call_model(spec, prompt, workdir)
    except subprocess.TimeoutExpired:
        rec.update(status="timeout", seconds=TIMEOUT, error=f"нет ответа за {TIMEOUT}с")
        return rec
    except Exception as e:  # noqa: BLE001
        rec.update(status="error", seconds=round(time.time() - t0, 1), error=repr(e))
        return rec

    rec["seconds"] = round(time.time() - t0, 1)
    rec["rc"] = rc
    body = out.strip()
    # ПУСТОЙ ИЛИ ОБРУБЛЕННЫЙ ОТВЕТ НЕ СЧИТАЕТСЯ ОТВЕТОМ: иначе судья поставит 1 модели,
    # которая просто не доехала по квоте, и мы обвиним модель в характере вместо рельсы.
    # Порог 400 замерен, а не угадан (прогон 03.08.2026): агентские CLI возвращают
    # преамбулу «сейчас подниму контекст и соберу scorecard...» на ~130 символов, а самый
    # короткий НАСТОЯЩИЙ ответ во всей матрице -- 560. Между ними пропасть, порог стоит
    # в ней. На пороге 120 две заглушки Grok проскочили как «ответы» и получили бы
    # честно низкую оценку за то, чего модель не писала.
    if len(body) < 400:
        # НАХОДКА ВНЕШНЕГО ЛОМАТЕЛЯ (Codex, 03.08): порог режет по длине, а краткость --
        # не порок; коротко-но-верный ответ на 399 символов исчез бы молча. Поэтому
        # отвергнутое НЕ выбрасывается: оно ложится рядом в _rejected/ вместе с длиной,
        # чтобы человек мог глазами отличить «модель была лаконична» от «рельса вернула
        # заглушку». В оценку по-прежнему не идёт -- но и невидимым не становится.
        rej = OUT / "_rejected"
        rej.mkdir(parents=True, exist_ok=True)
        (rej / f"{spec['code']}__{task['id']}.md").write_text(
            f"<!-- ОТВЕРГНУТО: {len(body)} символов < порога 400 | {spec['code']} | "
            f"{task['id']} -->\n\n{body or '(пусто)'}\n\n--- stderr ---\n{(err or '')[-800:]}",
            encoding="utf-8")
        rec.update(status="empty", error=(err or out)[-800:], chars=len(body),
                   rejected_file=str(rej / f"{spec['code']}__{task['id']}.md"))
        return rec

    dest.write_text(
        f"<!-- слепой код: {spec['code']} | задача: {task['id']} | "
        f"prompt_hash: {rec['prompt_hash']} | {rec['seconds']}с -->\n\n" + body,
        encoding="utf-8",
    )
    rec.update(status="ok", chars=len(body))
    return rec


def main():
    ap = argparse.ArgumentParser(description="одна персона на разных моделях")
    ap.add_argument("--only", help="коды моделей через запятую, напр. A,C")
    ap.add_argument("--task", help="id задач через запятую")
    ap.add_argument("--force", action="store_true", help="перезапросить, даже если файл есть")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    models = MODELS
    if args.only:
        want = {c.strip().upper() for c in args.only.split(",")}
        models = [m for m in MODELS if m["code"] in want]
    tasks = TASKS
    if args.task:
        want = {t.strip() for t in args.task.split(",")}
        tasks = [t for t in TASKS if t["id"] in want]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_KEY-SEALED.json").write_text(
        json.dumps({m["code"]: m["label"] for m in MODELS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "_prompt-substrate.md").write_text(
        "# Что получила КАЖДАЯ модель (байт-в-байт)\n\n"
        f"персона: {len(build_persona())} симв · память: {len(RECALL_SUBSTRATE)} симв\n\n"
        "## Замороженная память\n\n```\n" + RECALL_SUBSTRATE + "\n```\n",
        encoding="utf-8",
    )

    cells = [(m, t) for m in models for t in tasks]
    print(f"ячеек к прогону: {len(cells)} ({len(models)} моделей x {len(tasks)} задач)")
    results = []
    WORKROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ab-persona-", dir=str(WORKROOT)) as workdir:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(one_cell, m, t, workdir, args.force): (m, t) for m, t in cells}
            for f in as_completed(futs):
                m, t = futs[f]
                try:
                    rec = f.result()
                except Exception as e:  # noqa: BLE001
                    rec = {"code": m["code"], "task": t["id"], "status": "crash", "error": repr(e)}
                results.append(rec)
                mark = {"ok": "OK", "cached": "==", "empty": "ПУСТО"}.get(rec["status"], "СБОЙ")
                print(f"  [{mark:5}] {rec['code']} {t['id']:18} "
                      f"{rec.get('chars','?')}симв {rec.get('seconds','?')}с "
                      f"{rec.get('error','')[:110]}")

    runs_path = OUT / "runs.json"
    prev = []
    if runs_path.exists():
        try:
            prev = json.loads(runs_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    by_key = {(r["code"], r["task"]): r for r in prev if isinstance(r, dict) and "code" in r}
    for r in results:
        by_key[(r["code"], r["task"])] = r
    runs_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    # УБИРАЕМ ЗА СОБОЙ КОПИЮ УЧЁТКИ. Изоляция CODEX_HOME копирует ~/.codex/auth.json во
    # временный каталог -- это рабочий секрет, и он не должен пережить прогон и остаться
    # лежать на диске лишней копией. Чистим В КОДЕ, а не «не забыть руками»: забыть --
    # ровно то, что случается.
    if _CODEX_HOME_CACHE and _CODEX_HOME_CACHE.exists():
        shutil.rmtree(_CODEX_HOME_CACHE, ignore_errors=True)
        print(f"убрана временная копия учётки Codex: {_CODEX_HOME_CACHE}")

    okc = sum(1 for r in results if r["status"] in ("ok", "cached"))
    print(f"\nитог: {okc}/{len(results)} ячеек с ответом -> {OUT}")
    hashes = {r["prompt_hash"] for r in results if "prompt_hash" in r}
    print(f"уникальных prompt_hash на задачу должно быть по 1: всего {len(hashes)} "
          f"(ожидаем {len(tasks)})")
    return 0 if okc else 1


if __name__ == "__main__":
    sys.exit(main())

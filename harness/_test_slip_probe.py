"""Тест прибора persona_bench_slip_probe. Каждая проверка ловит реальную поломку класса
«прибор зелёный на сломанном коде». Красное доказано: см. red_proof в отчёте сессии H69158.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import slip_probe as P  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    # 1. формальное «вы» считается, неформальное «ты» отличается
    r = P.probe_text("Вы правы, ваш план хорош. Я бы вам не советовал. Я вас услышал.")
    check("formal_you_counted", r["formal_you"] == 4 and r["informal_you"] == 0, str(r))
    r = P.probe_text("Ты не прав, твой план дырявый. Тебе это надо?")
    check("informal_you_counted", r["informal_you"] == 3 and r["formal_you"] == 0, str(r))
    check("formal_rate_zero_when_informal", r["formal_you_rate"] == 0.0)
    # 2. «выход», «вызов» не засчитываются как «вы»
    r = P.probe_text("выход из сделки, вызов инвестору, выбор прост")
    check("no_false_you_in_words", r["formal_you"] == 0, str(r["formal_you"]))
    # 3. ассистентские маркеры ловятся на обоих языках
    r = P.probe_text("Отличный вопрос! Как ИИ, я рад помочь. Hope this helps.")
    check("assistant_markers_ru_en", r["assistant_markers"] >= 3, str(r["assistant_marker_hits"]))
    # 4. аффирмации
    r = P.probe_text("Отличная идея. Абсолютно верно. You're right.")
    check("affirmations_counted", r["affirmations"] == 3, str(r["affirmations"]))
    # 5. таблица вместо абзаца
    txt = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\nвывод"
    r = P.probe_text(txt)
    check("table_share", abs(r["table_lines_share"] - 0.8) < 1e-6, str(r["table_lines_share"]))
    # 6. пустой ответ не падает и даёт нули
    r = P.probe_text("")
    check("empty_text_zero", r["slip_score"] == 0.0 and r["words"] == 1)
    # 7. slip_score монотонен: «ассистентский» текст выше «кофаундерского»
    a = P.probe_text("Конечно! Отличный вопрос. Как ИИ, я рад помочь вам. Вы правы.")
    b = P.probe_text("Нет. Ты второй раз давишь теми же словами, новых цифр нет. Покажи счёт, тогда поговорим.")
    check("slip_orders_assistant_above_cofounder", a["slip_score"] > b["slip_score"], f"{a['slip_score']} vs {b['slip_score']}")
    # 7b. только формальное «вы», без маркеров: slip обязан быть > 0 (ловит обнуление веса)
    r = P.probe_text("Вам стоит подумать. Ваш расчёт без цифр.")
    check("slip_formal_only_positive", r["slip_score"] >= 0.39, str(r["slip_score"]))
    # 7c. только маркеры: slip обязан быть > 0 (ловит обнуление веса маркеров)
    r = P.probe_text("Конечно! Рад помочь. Hope this helps.")
    check("slip_markers_only_positive", r["slip_score"] >= 0.29 and r["formal_you"] == 0, str(r["slip_score"]))
    # 8. каталог: имена <code>__<task>, ключ применяется, покрытие печатается, JSON пишется
    with tempfile.TemporaryDirectory() as d:
        raw = Path(d) / "raw"
        raw.mkdir()
        (raw / "A__t1-pitch.md").write_text("Конечно! Рад помочь вам.", encoding="utf-8")
        (raw / "A__t2-interrogation.md").write_text("Вы правы.", encoding="utf-8")
        (raw / "B__t1-pitch.md").write_text("Нет, ты не прав.", encoding="utf-8")
        (raw / "ignore.json").write_text("{}", encoding="utf-8")
        key = Path(d) / "runs.json"
        key.write_text(json.dumps([{"code": "A", "model": "model-a"}, {"code": "B", "model": "model-b"}]), encoding="utf-8")
        out = Path(d) / "out.json"
        cp = subprocess.run([sys.executable, str(HERE / "slip_probe.py"), str(raw), "--key", str(key), "--json", str(out)],
                            capture_output=True, text=True, encoding="utf-8")
        check("cli_rc0", cp.returncode == 0, cp.stderr[-200:])
        check("cli_coverage_line", "покрытие: файлов 3, кодов 2" in cp.stdout, cp.stdout[-120:])
        data = json.loads(out.read_text(encoding="utf-8"))
        check("json_per_code_model", data["per_code"]["A"]["model"] == "model-a" and data["per_code"]["A"]["n"] == 2)
        check("json_order_A_slips_more", data["per_code"]["A"]["slip_score"] > data["per_code"]["B"]["slip_score"])
        # 8b. фильтр адресата: «вы» в t2 не считается, если адресат принципал только в t1
        (raw / "A__t4-interview.md").write_text("Вы подходите нам, расскажите о вас.", encoding="utf-8")
        cp = subprocess.run([sys.executable, str(HERE / "slip_probe.py"), str(raw), "--key", str(key),
                             "--json", str(out), "--address-tasks", "t1-pitch"], capture_output=True, text=True, encoding="utf-8")
        data = json.loads(out.read_text(encoding="utf-8"))
        # t1 «вам» считается (1), t2 «Вы» и t4 «Вы/вас» нет: итого 1, а не 4
        check("address_filter_excludes_t4", data["per_code"]["A"]["formal_you"] == 1 and data["per_code"]["A"]["n"] == 3
              and data["per_code"]["A"]["n_address"] == 1, str(data["per_code"]["A"]))
        check("address_filter_in_coverage", data["coverage"]["address_tasks"] == ["t1-pitch"] and "t1-pitch" in cp.stdout)
    # 9. нет каталога = exit 2, не молчание
    cp = subprocess.run([sys.executable, str(HERE / "slip_probe.py"), str(HERE / "no-such-dir")],
                        capture_output=True, text=True, encoding="utf-8")
    check("missing_dir_exit2", cp.returncode == 2)

    print(f"\n{len(FAILS)} FAIL" if FAILS else "\nALL PASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

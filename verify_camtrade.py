#!/usr/bin/env python3
"""CAMTRADE sanity check.

Runs a battery of checks against the CAMTRADE dataset and the decision
engine / analytics / business Q&A layers, without starting the HTTP
server. Prints a PASS/FAIL line per check and exits non-zero if anything
fails, so it can be used as a quick pre-demo smoke test:

    python verify_camtrade.py
"""

from __future__ import annotations

import sys
import traceback

import analytics
import business_qa
from decision_engine import (
    CamtradeData,
    annual_performance,
    credit_risk,
    customer_contributions,
    ebitda_analytical,
    expense_variance,
    find_database,
    product_margin_analysis,
    transport_analysis,
)

YEAR = 2025
PREVIOUS_YEAR = YEAR - 1

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        RESULTS.append((name, True, detail or "ok"))
    except Exception as exc:  # noqa: BLE001 - we want to report every failure, not just expected ones
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        RESULTS.append((f"{name} (traceback)", False, traceback.format_exc(limit=3)))


def main() -> int:
    print("=" * 78)
    print("CAMTRADE VERIFICATION")
    print("=" * 78)

    # --- database / schema -------------------------------------------------
    def db_exists():
        path = find_database()
        assert path.is_file(), f"database not found at {path}"
        return str(path)

    check("Database file exists", db_exists)

    expected_tables = {"sales", "purchases", "expenses", "customers", "products", "receivables", "inventory"}

    def tables_present():
        data = CamtradeData(find_database())
        try:
            missing = expected_tables - {t for t in expected_tables if data.has_table(t)}
            assert not missing, f"missing tables: {missing}"
            counts = {t: data.scalar(f"SELECT COUNT(*) FROM {t}") for t in expected_tables}
            empty = [t for t, n in counts.items() if n == 0]
            assert not empty, f"empty tables: {empty}"
            return ", ".join(f"{t}={int(n)}" for t, n in counts.items())
        finally:
            data.close()

    check("All expected tables present and non-empty", tables_present)

    # --- decision engine -----------------------------------------------------
    def engine_runs():
        data = CamtradeData(find_database())
        try:
            current = ebitda_analytical(annual_performance(data, YEAR))
            previous = ebitda_analytical(annual_performance(data, PREVIOUS_YEAR))
            assert current["revenue"] > 0, "current year revenue is zero"
            assert previous["revenue"] > 0, "previous year revenue is zero"
            return f"revenue {YEAR}={current['revenue']:,.0f} FCFA, EBITDA margin={current['ebitda_margin_pct']:.1f}%"
        finally:
            data.close()

    check("decision_engine: annual_performance / ebitda_analytical run for both years", engine_runs)

    def customers_run():
        data = CamtradeData(find_database())
        try:
            rows, method = customer_contributions(data, YEAR)
            assert rows, "customer_contributions returned no rows"
            assert method, "customer_contributions returned no method label"
            return f"{len(rows)} customers, method={method}"
        finally:
            data.close()

    check("decision_engine: customer_contributions returns rows + evidence method", customers_run)

    def products_run():
        data = CamtradeData(find_database())
        try:
            rows, method, is_proxy = product_margin_analysis(data, YEAR)
            assert rows, "product_margin_analysis returned no rows"
            assert is_proxy is True, "product margin should always be flagged as a proxy"
            return f"{len(rows)} products, method={method}"
        finally:
            data.close()

    check("decision_engine: product_margin_analysis returns rows + is flagged as proxy", products_run)

    def expenses_run():
        data = CamtradeData(find_database())
        try:
            rows, method = expense_variance(data, YEAR, PREVIOUS_YEAR)
            assert rows, "expense_variance returned no rows"
            return f"{len(rows)} expense categories, method={method}"
        finally:
            data.close()

    check("decision_engine: expense_variance returns rows", expenses_run)

    def transport_and_receivables_run():
        data = CamtradeData(find_database())
        try:
            transport = transport_analysis(data, YEAR)
            receivables = credit_risk(data, limit=10)
            assert transport, "transport_analysis returned no rows"
            assert receivables, "credit_risk returned no rows"
            return f"transport rows={len(transport)}, receivables rows={len(receivables)}"
        finally:
            data.close()

    check("decision_engine: transport_analysis and credit_risk return rows", transport_and_receivables_run)

    # --- analytics layer -------------------------------------------------
    def analytics_cross_check():
        a = analytics.annual_performance(YEAR)
        data = CamtradeData(find_database())
        try:
            b = annual_performance(data, YEAR)
        finally:
            data.close()
        assert abs(a["revenue"] - b["revenue"]) < 1, "analytics.py and decision_engine.py disagree on revenue"
        assert abs(a["cogs"] - b["cogs"]) < 1, "analytics.py and decision_engine.py disagree on COGS"
        return "analytics.py values match decision_engine.py for the same year (shared source of truth)"

    check("analytics.py reuses decision_engine.py (no duplicated financial logic)", analytics_cross_check)

    def stock_alerts_run():
        rows = analytics.stock_alerts()
        return f"{len(rows)} SKU(s) flagged" if rows else "no SKUs flagged (not an error)"

    check("analytics.stock_alerts runs without error", stock_alerts_run)

    def receivables_risk_run():
        rows = analytics.receivables_risk()
        return f"{len(rows)} customer(s) overdue" if rows else "no overdue customers (not an error)"

    check("analytics.receivables_risk runs without error", receivables_risk_run)

    # --- Business Q&A: the 6 required demo questions -----------------------
    for q in business_qa.CANONICAL_QUESTIONS:

        def ask(question=q["text"]):
            result = business_qa.answer(question, year=YEAR)
            assert result["evidence"] in ("MEASURED", "ESTIMATED", "NO DATA"), \
                f"unexpected evidence level: {result['evidence']}"
            assert result["evidence"] != "NO DATA", "canonical business question was not recognised"
            assert result["answer"], "empty answer"
            return f"[{result['evidence']}] {result['answer'][:100]}..."

        check(f"business_qa: '{q['text']}'", ask)

    # --- Business Q&A: must correctly decline out-of-scope questions -------
    def out_of_scope():
        result = business_qa.answer("What is the weather in Douala tomorrow?", year=YEAR)
        assert result["evidence"] == "NO DATA", "out-of-scope question should be flagged NO DATA"
        return result["answer"][:100] + "..."

    check("business_qa: out-of-scope question is correctly declined (NO DATA)", out_of_scope)

    def empty_question():
        result = business_qa.answer("", year=YEAR)
        assert result["evidence"] == "NO DATA"
        return "ok"

    check("business_qa: empty question is handled without crashing", empty_question)

    # --- dashboard rendering (no live server needed) ------------------------
    def dashboard_renders():
        import camtrade_dashboard as dash
        data = dash.gather_all(YEAR)
        page = dash.render(data, demo_mode=False)
        demo_page = dash.render(data, demo_mode=True)
        assert "<html" in page and "</html>" in page, "dashboard page is not well-formed HTML"
        assert "STEP 1" in demo_page and "STEP 9" in demo_page, "demo mode is missing step markers"
        assert "STEP 1" not in page, "step markers should not appear outside demo mode"
        return f"dashboard {len(page):,} bytes, demo {len(demo_page):,} bytes"

    check("camtrade_dashboard: both / and /demo render as valid HTML", dashboard_renders)

    # --- summary -------------------------------------------------------------
    print()
    failures = 0
    for name, ok, detail in RESULTS:
        status = "PASS" if ok else "FAIL"
        if not ok and "(traceback)" not in name:
            failures += 1
        print(f"[{status}] {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {line}")

    print()
    print("=" * 78)
    total = len([r for r in RESULTS if "(traceback)" not in r[0]])
    print(f"{total - failures}/{total} checks passed" if failures == 0
          else f"{total - failures}/{total} checks passed — {failures} FAILURE(S)")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

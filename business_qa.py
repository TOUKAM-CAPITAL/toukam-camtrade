#!/usr/bin/env python3
"""CAMTRADE Business Q&A.

Maps natural-language business questions (French or English) to the
decision-engine computations that can actually answer them, and returns a
structured answer that always states its evidence level:

  MEASURED  — read/summed directly from the database, no modelling involved
  ESTIMATED — derived via a disclosed proxy/allocation method
  NO DATA   — the question cannot be answered reliably with data on hand

This module never invents a number. If a question falls outside what the
CAMTRADE tables support, it says so and names the data that would be needed.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from decision_engine import (
    CamtradeData,
    DEFAULT_YEAR,
    annual_performance,
    change,
    credit_risk,
    customer_contributions,
    ebitda_analytical,
    expense_variance,
    find_database,
    product_margin_analysis,
    transport_analysis,
)


def money(value: float) -> str:
    return f"{float(value or 0):,.0f} FCFA".replace(",", " ")


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


# The six canonical questions from the demo script (French, as specified),
# each paired with a short English label for the UI quick-question chips.
CANONICAL_QUESTIONS: list[dict[str, str]] = [
    {"id": "ebitda_decline", "label": "Why did EBITDA decline while revenue grew?",
     "text": "Pourquoi l'EBITDA a-t-il baissé alors que le chiffre d'affaires a augmenté ?"},
    {"id": "product_margin", "label": "Which products destroy the most margin?",
     "text": "Quels produits détruisent le plus de marge ?"},
    {"id": "customer_profit", "label": "Which customers are least profitable?",
     "text": "Quels clients sont les moins rentables ?"},
    {"id": "transport_rising", "label": "Why are transport costs increasing?",
     "text": "Pourquoi les coûts de transport augmentent-ils ?"},
    {"id": "expense_growth", "label": "Which expenses grew the most?",
     "text": "Quelles dépenses ont le plus augmenté ?"},
    {"id": "receivables_risk", "label": "Which customers carry the most receivables risk?",
     "text": "Quels clients présentent le plus grand risque de créances ?"},
]


def _fold(text: str) -> str:
    """Lowercase and strip accents so 'détruisent' matches keyword 'detru'."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _match(question: str, all_of: tuple[tuple[str, ...], ...]) -> bool:
    """True if every keyword group has at least one hit in the question."""
    q = _fold(question)
    return all(any(_fold(kw) in q for kw in group) for group in all_of)


def answer(question: str, year: int = DEFAULT_YEAR, previous_year: int | None = None) -> dict[str, Any]:
    previous_year = previous_year if previous_year is not None else year - 1
    q = (question or "").strip()
    if not q:
        return _out_of_scope(q)

    data = CamtradeData(find_database())
    try:
        if _match(q, (("ebitda",), ("baiss", "declin", "dropped", "decreased", "fell", "down", "pourquoi", "why"))):
            return _why_ebitda_declined(data, year, previous_year)
        if _match(q, (("produit", "product"), ("marge", "margin"), ("detru", "destroy", "pire", "worst", "least"))):
            return _product_margin_destroyers(data, year)
        if _match(q, (("client", "customer"), ("rentable", "profitab", "least", "moins"))):
            return _least_profitable_customers(data, year)
        if _match(q, (("transport",), ("augment", "increas", "rising", "hausse", "pourquoi", "why"))):
            return _why_transport_rising(data, year, previous_year)
        if _match(q, (("depense", "dépense", "expense", "cost", "coûts", "couts"), ("augment", "increas", "grew", "grow", "most", "plus"))):
            return _biggest_expense_increases(data, year, previous_year)
        if _match(q, (("creance", "créance", "receivable"), ("risque", "risk"))):
            return _receivables_risk(data)
        return _out_of_scope(q)
    finally:
        data.close()


def _why_ebitda_declined(data: CamtradeData, year: int, previous_year: int) -> dict[str, Any]:
    current = ebitda_analytical(annual_performance(data, year))
    previous = ebitda_analytical(annual_performance(data, previous_year))

    revenue_delta = current["revenue"] - previous["revenue"]
    ebitda_delta = current["ebitda"] - previous["ebitda"]
    revenue_grew = revenue_delta >= 0
    ebitda_moved = "declined" if ebitda_delta < 0 else "improved"

    components = [
        {"label": "Revenue", "key": "revenue", "sign": 1, "evidence": "MEASURED"},
        {"label": "COGS — product cost (proxy)", "key": "cogs_product_cost", "sign": -1, "evidence": "ESTIMATED"},
        {"label": "COGS — inbound freight (proxy)", "key": "cogs_inbound_freight", "sign": -1, "evidence": "ESTIMATED"},
        {"label": "Sales transport", "key": "sales_transport", "sign": -1, "evidence": "MEASURED"},
        {"label": "Operating expenses", "key": "operating_expenses", "sign": -1, "evidence": "MEASURED"},
    ]
    rows = []
    for c in components:
        old, new = previous[c["key"]], current[c["key"]]
        impact = (new - old) * c["sign"]
        rows.append({"label": c["label"], "previous": old, "current": new,
                     "change": new - old, "ebitda_impact": impact, "evidence": c["evidence"]})
    cost_rows = [r for r in rows if r["label"] != "Revenue"]
    drivers = sorted(cost_rows, key=lambda r: r["ebitda_impact"])[:2]

    driver_text = " and ".join(
        f"{d['label'].lower()} rose {money(d['change'])} (an EBITDA impact of {money(d['ebitda_impact'])})"
        for d in drivers if d["ebitda_impact"] < 0
    ) or "no single cost line dominates the movement"

    narrative = (
        f"Revenue {'grew' if revenue_grew else 'declined'} {money(abs(revenue_delta))} "
        f"({pct(change(previous['revenue'], current['revenue']))}) between {previous_year} and {year}, "
        f"while EBITDA {ebitda_moved} from {money(previous['ebitda'])} to {money(current['ebitda'])} "
        f"({pct(change(previous['ebitda'], current['ebitda']))}). "
        f"The main driver(s): {driver_text}. "
        f"EBITDA margin moved from {previous['ebitda_margin_pct']:.1f}% to {current['ebitda_margin_pct']:.1f}%."
    )

    return {
        "question": "Pourquoi l'EBITDA a-t-il baissé alors que le chiffre d'affaires a augmenté ?",
        "evidence": "ESTIMATED",
        "evidence_detail": (
            "Revenue, sales transport and operating expenses are MEASURED directly from the sales/expenses "
            "tables. COGS is an ESTIMATED proxy (weighted-average purchase cost + freight per unit applied to "
            "units sold) because CAMTRADE has no per-sale product cost, opening/closing inventory or a "
            "documented costing rule. The overall answer is therefore labelled ESTIMATED — it is only as solid "
            "as the COGS proxy."
        ),
        "answer": narrative,
        "columns": ["label", "previous", "current", "change", "ebitda_impact", "evidence"],
        "data": rows,
    }


def _product_margin_destroyers(data: CamtradeData, year: int) -> dict[str, Any]:
    products, method, is_proxy = product_margin_analysis(data, year)
    if not products:
        return _no_data(
            "Quels produits détruisent le plus de marge ?",
            "Product-level margin requires sales.product_id, sales.quantity and sales.revenue, none of which "
            "are available in the current dataset.",
        )
    worst = [p for p in products if p["margin"] < 0] or products[:5]
    total_negative = sum(p["margin"] for p in products if p["margin"] < 0)
    narrative = (
        f"{len(worst)} product line(s) show a negative estimated margin in {year}, "
        f"a combined estimated margin drag of {money(total_negative)}. "
        f"Worst: {worst[0]['product_name']} ({money(worst[0]['margin'])}, {worst[0]['margin_pct']:.1f}% margin)."
        if any(p["margin"] < 0 for p in products) else
        f"No product shows a negative estimated margin in {year}. Lowest margin: "
        f"{products[0]['product_name']} at {products[0]['margin_pct']:.1f}%."
    )
    return {
        "question": "Quels produits détruisent le plus de marge ?",
        "evidence": "ESTIMATED",
        "evidence_detail": f"Method: {method}. This is a cost allocation, not the company's real per-product cost accounting.",
        "answer": narrative,
        "columns": ["product_name", "units_sold", "revenue", "allocated_cost", "margin", "margin_pct"],
        "data": products[:8],
    }


def _least_profitable_customers(data: CamtradeData, year: int) -> dict[str, Any]:
    customers, method = customer_contributions(data, year)
    if not customers:
        return _no_data(
            "Quels clients sont les moins rentables ?",
            "Customer profitability requires sales.customer_id, sales.revenue and a sale date, none of which "
            "are available in the current dataset.",
        )
    worst = customers[:5]
    is_proxy = method.startswith("proxy")
    narrative = (
        f"Lowest estimated contribution: {worst[0]['customer_name']} at {money(worst[0]['contribution'])} "
        f"({worst[0]['margin_pct']:.1f}% margin) on {money(worst[0]['revenue'])} revenue. "
        f"{len(worst)} customers shown below, sorted from least to most profitable."
    )
    return {
        "question": "Quels clients sont les moins rentables ?",
        "evidence": "ESTIMATED" if is_proxy else "MEASURED",
        "evidence_detail": (
            f"Method: {method}. " + (
                "No direct per-sale cost is recorded per customer, so COGS is allocated pro-rata to revenue — "
                "use this to prioritise a review, not to drop a customer."
                if is_proxy else
                "Direct cost figures were found on the sales rows; this is measured, not allocated."
            )
        ),
        "answer": narrative,
        "columns": ["customer_name", "revenue", "transport", "contribution", "margin_pct"],
        "data": worst,
    }


def _why_transport_rising(data: CamtradeData, year: int, previous_year: int) -> dict[str, Any]:
    current = annual_performance(data, year)
    previous = annual_performance(data, previous_year)
    sales_transport_delta = current["sales_transport"] - previous["sales_transport"]
    sales_transport_var = change(previous["sales_transport"], current["sales_transport"])

    expense_rows, ev_method = expense_variance(data, year, previous_year)
    transport_opex = next((r for r in expense_rows if r["expense_type"].lower().startswith("transport")), None)

    top_transport_customers = transport_analysis(data, year)

    parts = []
    if sales_transport_var is not None:
        parts.append(
            f"delivery cost allocated on sales rose {money(sales_transport_delta)} "
            f"({pct(sales_transport_var)}) between {previous_year} and {year}"
        )
    if transport_opex:
        parts.append(
            f"the 'Transport' operating-expense line rose {money(transport_opex['change'])} "
            f"({pct(transport_opex['variation_pct'])}) over the same period"
        )
    narrative = "Both measured signals point the same way: " + "; and ".join(parts) + "." if parts else \
        "No transport cost data is available for comparison."
    if top_transport_customers:
        narrative += (
            f" The customer concentrating the most delivery cost is {top_transport_customers[0]['customer_name']} "
            f"({money(top_transport_customers[0]['transport'])})."
        )
    narrative += (
        " These figures are measured totals, not a root cause. To pin down WHY (fuel prices, carrier rates, "
        "route inefficiency, order-size mix) additional data is required: fuel price index, carrier invoices/rate "
        "cards, and delivery route logs — none of which exist in the current dataset (NO DATA)."
    )

    rows = []
    if sales_transport_var is not None:
        rows.append({"label": "Sales-allocated transport (from sales)", "previous": previous["sales_transport"],
                     "current": current["sales_transport"], "change": sales_transport_delta,
                     "variation_pct": sales_transport_var, "evidence": "MEASURED"})
    if transport_opex:
        rows.append({"label": "Transport operating expense (from expenses)", "previous": transport_opex["previous"],
                     "current": transport_opex["current"], "change": transport_opex["change"],
                     "variation_pct": transport_opex["variation_pct"], "evidence": "MEASURED"})

    return {
        "question": "Pourquoi les coûts de transport augmentent-ils ?",
        "evidence": "MEASURED" if rows else "NO DATA",
        "evidence_detail": (
            "The size of the increase is MEASURED from two independent sources (sales-allocated transport and "
            "the Transport expense category). The operational root cause is NO DATA — CAMTRADE does not record "
            "fuel prices, carrier rates or route logs."
        ),
        "answer": narrative,
        "columns": ["label", "previous", "current", "change", "variation_pct", "evidence"],
        "data": rows,
        "supporting": {"top_customers_by_transport": top_transport_customers[:5]},
    }


def _biggest_expense_increases(data: CamtradeData, year: int, previous_year: int) -> dict[str, Any]:
    rows, method = expense_variance(data, year, previous_year)
    if not rows:
        return _no_data(
            "Quelles dépenses ont le plus augmenté ?",
            "Expense variance requires the expenses table with a category, amount and date column, none of "
            "which are available in the current dataset.",
        )
    increases = [r for r in rows if r["change"] > 0]
    top = increases[:5] or rows[:5]
    narrative = (
        f"Biggest increase: {top[0]['expense_type']}, up {money(top[0]['change'])} "
        f"({pct(top[0]['variation_pct'])}) between {previous_year} and {year}. "
        f"{len(increases)} of {len(rows)} expense categories increased."
    )
    return {
        "question": "Quelles dépenses ont le plus augmenté ?",
        "evidence": "MEASURED",
        "evidence_detail": f"{method}.",
        "answer": narrative,
        "columns": ["expense_type", "previous", "current", "change", "variation_pct"],
        "data": rows,
    }


def _receivables_risk(data: CamtradeData) -> dict[str, Any]:
    rows = credit_risk(data, limit=10)
    if not rows:
        return _no_data(
            "Quels clients présentent le plus grand risque de créances ?",
            "Receivables risk requires the receivables table with customer_id, outstanding_balance and "
            "days_overdue, none of which are available in the current dataset.",
        )
    total_outstanding = sum(float(r["outstanding_balance"] or 0) for r in rows)
    narrative = (
        f"{len(rows)} customers are overdue, totalling {money(total_outstanding)} outstanding. "
        f"Highest risk: {rows[0]['customer_name']} — {money(rows[0]['outstanding_balance'])} outstanding, "
        f"{int(rows[0]['days_overdue'] or 0)} days overdue."
    )
    return {
        "question": "Quels clients présentent le plus grand risque de créances ?",
        "evidence": "MEASURED",
        "evidence_detail": "Read directly from the receivables table (outstanding_balance, days_overdue); no modelling applied.",
        "answer": narrative,
        "columns": ["customer_name", "outstanding_balance", "days_overdue"],
        "data": rows,
    }


def _no_data(question: str, detail: str) -> dict[str, Any]:
    return {
        "question": question, "evidence": "NO DATA", "evidence_detail": detail,
        "answer": "I can't answer this reliably — the data required isn't available. " + detail,
        "columns": [], "data": [],
    }


def _out_of_scope(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "evidence": "NO DATA",
        "evidence_detail": (
            "This question doesn't match a supported analysis. CAMTRADE Business Q&A currently covers: "
            "EBITDA drivers, product margin, customer profitability, transport cost trends, expense variance, "
            "and receivables risk — each computed from the sales, purchases, expenses, customers, products and "
            "receivables tables."
        ),
        "answer": (
            "I can't answer this reliably with the data available. This question is outside the scope of what "
            "the current CAMTRADE dataset supports (sales, purchases, expenses, customers, products, "
            "receivables, inventory). Ask about EBITDA, product margin, customer profitability, transport "
            "costs, expense growth, or receivables risk."
        ),
        "columns": [], "data": [],
    }


if __name__ == "__main__":
    for q in CANONICAL_QUESTIONS:
        result = answer(q["text"])
        print(f"\nQ: {q['text']}")
        print(f"[{result['evidence']}] {result['answer']}")
    print("\nQ: What is the weather in Douala tomorrow?")
    print(answer("What is the weather in Douala tomorrow?")["answer"])

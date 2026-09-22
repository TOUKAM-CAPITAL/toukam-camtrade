#!/usr/bin/env python3
"""TOUKAM BUSINESS INTELLIGENCE — CAMTRADE executive dashboard.

AI-powered Business Intelligence for Finance & Operations.

This dashboard deliberately uses the public functions exposed by
decision_engine.py and analytics.py. It does not duplicate financial
calculations or turn analytical proxies into accounting facts: every figure
on the page is labelled MEASURED (read/summed directly from the database),
ESTIMATED (a disclosed proxy/allocation method), or the underlying function
returns nothing and the section says so (NO DATA).

Routes
------
  GET /            executive dashboard
  GET /demo        same dashboard, with a guided 9-step commercial demo path
  GET /api/qa      Business Q&A — ?q=<question>[&year=YYYY]      -> JSON
  GET /api/health  liveness check                                 -> JSON
"""

from __future__ import annotations

import html
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

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

# HOST/PORT: when this runs on a hosting platform (Render, Railway, etc.), the
# platform sets the PORT environment variable and expects the app to bind
# 0.0.0.0 (all interfaces), not just the local machine. Locally (double-click
# launch_camtrade.bat), PORT is never set, so this falls back to the exact
# same 127.0.0.1:8000 behavior the dashboard always had -- nothing changes
# for Romeo's own machine.
_RUNNING_ON_A_HOST = "PORT" in os.environ  # set by hosting platforms, never by launch_camtrade.bat
HOST = "0.0.0.0" if _RUNNING_ON_A_HOST else "127.0.0.1"
PORT = int(os.environ.get("PORT", 8000))
YEAR = 2025


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------

def money(value: float) -> str:
    return f"{float(value or 0):,.0f} FCFA".replace(",", " ")


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def variation(old: float, new: float) -> float | None:
    return None if old == 0 else (new - old) / abs(old) * 100


def badge(level: str) -> str:
    level = (level or "NO DATA").upper()
    css = {"MEASURED": "badge-measured", "ESTIMATED": "badge-estimated"}.get(level, "badge-nodata")
    label = level if level in ("MEASURED", "ESTIMATED") else "NO DATA"
    return f'<span class="badge {css}">{label}</span>'


# ----------------------------------------------------------------------
# Data assembly
# ----------------------------------------------------------------------

def build_recommendations(current, previous, customer_method, transport, receivables,
                           negative_margin_products, stock) -> list[str]:
    recommendations: list[str] = []
    if current["ebitda"] < previous["ebitda"]:
        recommendations.append(
            "Reconcile the EBITDA decline against revenue, purchase cost, freight, transport and "
            "operating-expense movements before taking any commercial action."
        )
    if negative_margin_products:
        names = ", ".join(p["product_name"] for p in negative_margin_products[:3])
        recommendations.append(
            f"Review pricing and sourcing for margin-negative products ({names}) — confirm with real "
            f"per-product cost before repricing or delisting."
        )
    if customer_method.startswith("proxy"):
        recommendations.append(
            "Build real per-customer cost of goods sold. Do not negotiate terms with, or drop, a customer "
            "on the allocated-COGS proxy alone."
        )
    if transport:
        recommendations.append(
            "Review delivery routes, minimum order sizes and delivery terms for the customers concentrating "
            "the highest transport cost."
        )
    if receivables:
        recommendations.append(
            "Prioritise collection on overdue receivables and set up a weekly review of at-risk balances."
        )
    if stock:
        recommendations.append(
            f"{len(stock)} SKU(s) are flagged in inventory (stockout risk or dormant stock) — review before "
            f"they constrain sales or tie up working capital."
        )
    recommendations.append(
        "Validate this diagnostic with finance/controlling, then assign an owner and a review date to each action."
    )
    return recommendations


def gather_all(year: int = YEAR) -> dict:
    """Build the full dashboard view model from the decision engine and analytics layer."""
    data = CamtradeData(find_database())
    try:
        current = ebitda_analytical(annual_performance(data, year))
        previous = ebitda_analytical(annual_performance(data, year - 1))
        customers, customer_method = customer_contributions(data, year)
        transport = transport_analysis(data, year)
        receivables = credit_risk(data, limit=8)
        products, product_method, _ = product_margin_analysis(data, year)
        expenses, expense_method = expense_variance(data, year, year - 1)
    finally:
        data.close()

    stock = analytics.stock_alerts()

    bridge_components = [
        ("Revenue", "revenue", 1, "MEASURED"),
        ("COGS — product cost", "cogs_product_cost", -1, "ESTIMATED"),
        ("COGS — inbound freight", "cogs_inbound_freight", -1, "ESTIMATED"),
        ("Sales transport", "sales_transport", -1, "MEASURED"),
        ("Operating expenses", "operating_expenses", -1, "MEASURED"),
        ("EBITDA (analytical)", "ebitda", 1, "ESTIMATED"),
    ]
    bridge = []
    for label, key, sign, evidence in bridge_components:
        old, new = previous[key], current[key]
        bridge.append({
            "label": label, "previous": old, "current": new, "change": new - old,
            "variation": variation(old, new), "ebitda_impact": (new - old) * sign, "evidence": evidence,
        })
    cost_rows = [r for r in bridge if r["label"] not in ("Revenue", "EBITDA (analytical)")]
    main_driver = min(cost_rows, key=lambda r: r["ebitda_impact"]) if cost_rows else None

    receivables_total = sum(float(r.get("outstanding_balance") or 0) for r in receivables)
    negative_margin_products = [p for p in products if p["margin"] < 0]

    revenue_direction = "grew" if current["revenue"] >= previous["revenue"] else "declined"
    ebitda_direction = "grew" if current["ebitda"] >= previous["ebitda"] else "declined"
    summary = (
        f"In {year}, revenue {revenue_direction} to {money(current['revenue'])} while analytical EBITDA "
        f"{ebitda_direction} to {money(current['ebitda'])} ({current['ebitda_margin_pct']:.1f}% margin, "
        f"vs {previous['ebitda_margin_pct']:.1f}% in {year - 1})."
    )

    return {
        "year": year, "previous_year": year - 1, "current": current, "previous": previous,
        "bridge": bridge, "main_driver": main_driver,
        "customers": customers[:8], "customer_method": customer_method,
        "customer_is_proxy": customer_method.startswith("proxy"),
        "transport": transport, "receivables": receivables, "receivables_total": receivables_total,
        "products": products[:8], "product_method": product_method,
        "negative_margin_products": negative_margin_products,
        "expenses": expenses, "expense_method": expense_method, "stock": stock,
        "recommendations": build_recommendations(
            current, previous, customer_method, transport, receivables, negative_margin_products, stock
        ),
        "additional_data_needed": [
            "Per-sale product cost (actual COGS) to replace the weighted-purchase-cost proxy used throughout this report.",
            "Opening and closing inventory with a documented valuation rule (FIFO/weighted average) to turn COGS into an accounting figure.",
            "Carrier invoices, a fuel price index and delivery route logs — to explain WHY transport costs are rising, not just by how much.",
            "Customer-level payment history over time (not just a point-in-time balance) to model receivables risk as a trend.",
        ],
        "summary": summary,
    }


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

STYLE = """
:root{--bg:#0c1015;--panel:#151b23;--panel2:#1c2530;--text:#edf1f5;--muted:#a5b0bc;--line:#2c3743;
--gold:#d8b55b;--good:#63c28e;--bad:#ef8383;--nodata:#7c8794}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Segoe UI,Arial,sans-serif}
.wrap{max-width:1280px;margin:auto;padding:26px 22px 60px}
.header{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px;flex-wrap:wrap}
.brand{color:var(--gold);font-size:12px;font-weight:700;letter-spacing:.12em}
h1{margin:7px 0;font-size:28px}
h2{font-size:18px;margin:30px 0 12px;display:flex;align-items:center;gap:10px}
.step-tag{font-size:10px;font-weight:700;letter-spacing:.06em;color:#17130a;background:var(--gold);
border-radius:5px;padding:3px 7px;white-space:nowrap}
.muted,.note{color:var(--muted);font-size:13px}
button,.btn{background:var(--gold);color:#17130a;border:0;border-radius:8px;padding:10px 14px;
font-weight:700;cursor:pointer;font-size:13px}
.btn-ghost{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:13px}
.card{padding:18px}
.label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.value{font-size:24px;font-weight:750;margin-top:8px}
.small{margin-top:8px;font-size:12px;color:var(--muted)}
.up{color:var(--good)}.down{color:var(--bad)}
.panel{overflow:hidden}
.insight{padding:16px;border-left:3px solid var(--gold);background:var(--panel2);margin:12px;border-radius:7px}
.warning{margin:0;padding:14px 16px;background:#3b2d17;color:#ffe5a8;font-size:12px;line-height:1.5}
.note{padding:14px 16px;border-top:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:11px 13px;border-bottom:1px solid var(--line);text-align:left}
th{background:var(--panel2);color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border:0}
ol{margin:0;padding:18px 18px 18px 38px}li{margin:0 0 11px;line-height:1.45}
ul.plain{list-style:none;margin:0;padding:14px 16px}
ul.plain li{padding:8px 0;border-bottom:1px solid var(--line);line-height:1.4}
ul.plain li:last-child{border:0}
.footer{color:#71808d;font-size:11px;margin-top:30px}
.badge{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.04em;padding:3px 8px;border-radius:20px;white-space:nowrap}
.badge-measured{background:rgba(99,194,142,.16);color:var(--good);border:1px solid rgba(99,194,142,.4)}
.badge-estimated{background:rgba(216,181,91,.16);color:var(--gold);border:1px solid rgba(216,181,91,.4)}
.badge-nodata{background:rgba(124,135,148,.16);color:var(--nodata);border:1px solid rgba(124,135,148,.4)}
.demo-banner{background:linear-gradient(90deg,#2a2210,#151b23);border:1px solid var(--gold);border-radius:12px;
padding:14px 18px;margin-bottom:18px;font-size:13px}
.demo-nav{position:sticky;top:0;z-index:5;display:flex;gap:6px;flex-wrap:wrap;background:var(--bg);
padding:10px 0;margin-bottom:6px;border-bottom:1px solid var(--line)}
.demo-nav a{color:var(--muted);text-decoration:none;font-size:11px;background:var(--panel2);
padding:6px 10px;border-radius:16px;border:1px solid var(--line)}
.demo-nav a:hover{color:var(--text);border-color:var(--gold)}
.talking-point{margin:8px 12px 0;padding:10px 12px;border:1px dashed var(--line);border-radius:8px;
font-size:12px;color:var(--muted);font-style:italic}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:14px 16px 4px}
.qa-form{display:flex;gap:8px;padding:0 16px 16px}
.qa-form input{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;
color:var(--text);padding:10px 12px;font-size:13px}
#qa-result{padding:0 0 6px}
@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}}
@media(max-width:520px){.grid{grid-template-columns:1fr}.header{align-items:flex-start;flex-direction:column}
.qa-form{flex-direction:column}}
"""

SCRIPT = """
function fmt(v){
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number'){
    if (Number.isInteger(v)) return v.toLocaleString('en-US');
    return Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1);
  }
  return v;
}
function badgeClass(level){
  return {MEASURED:'badge-measured', ESTIMATED:'badge-estimated'}[level] || 'badge-nodata';
}
async function askQA(question){
  document.getElementById('qa-question').value = question;
  const box = document.getElementById('qa-result');
  box.innerHTML = '<div class="muted" style="padding:14px 16px">Thinking…</div>';
  try {
    const res = await fetch('/api/qa?q=' + encodeURIComponent(question));
    const data = await res.json();
    renderQA(data);
  } catch (e) {
    box.innerHTML = '<div class="warning">Request failed: ' + e + '</div>';
  }
}
function renderQA(data){
  const box = document.getElementById('qa-result');
  let out = '<div class="insight"><span class="badge ' + badgeClass(data.evidence) + '">' + data.evidence +
    '</span><p style="margin:10px 0 4px">' + data.answer + '</p>' +
    '<p class="muted" style="margin:0">' + data.evidence_detail + '</p></div>';
  if (data.columns && data.columns.length && data.data && data.data.length){
    out += '<div class="panel" style="margin:0 12px 12px;border-radius:9px"><table><thead><tr>';
    for (const c of data.columns) out += '<th>' + c.replace(/_/g,' ') + '</th>';
    out += '</tr></thead><tbody>';
    for (const row of data.data){
      out += '<tr>';
      for (const c of data.columns){
        let v = row[c];
        if (c === 'evidence' && typeof v === 'string') v = '<span class="badge ' + badgeClass(v) + '">' + v + '</span>';
        else v = fmt(v);
        out += '<td>' + v + '</td>';
      }
      out += '</tr>';
    }
    out += '</tbody></table></div>';
  }
  box.innerHTML = out;
}
document.addEventListener('DOMContentLoaded', function(){
  const form = document.getElementById('qa-form');
  if (form) form.addEventListener('submit', function(e){
    e.preventDefault();
    const q = document.getElementById('qa-question').value.trim();
    if (q) askQA(q);
  });
});
"""


def step_tag(n: int, demo_mode: bool) -> str:
    return f'<span class="step-tag">STEP {n}</span>' if demo_mode else ""


def talking_point(text: str, demo_mode: bool) -> str:
    return f'<div class="talking-point">\U0001F4AC {html.escape(text)}</div>' if demo_mode else ""


def render(data: dict, demo_mode: bool = False) -> str:
    current, previous = data["current"], data["previous"]
    revenue_delta = current["revenue"] - previous["revenue"]
    ebitda_delta = current["ebitda"] - previous["ebitda"]
    customers = data["customers"]
    products = data["products"]

    bridge_rows = "".join(
        f"<tr><td>{html.escape(r['label'])} {badge(r['evidence'])}</td><td>{money(r['previous'])}</td>"
        f"<td>{money(r['current'])}</td>"
        f"<td class=\"{'up' if r['ebitda_impact'] >= 0 else 'down'}\">{money(r['ebitda_impact'])}</td>"
        f"<td>{percent(r['variation'])}</td></tr>" for r in data["bridge"]
    )
    product_rows = "".join(
        f"<tr><td>{html.escape(str(p['product_name']))}</td><td>{p['units_sold']:,.0f}</td>"
        f"<td>{money(p['revenue'])}</td><td>{money(p['allocated_cost'])}</td>"
        f"<td class=\"{'up' if p['margin'] >= 0 else 'down'}\">{money(p['margin'])}</td>"
        f"<td>{p['margin_pct']:.1f}%</td></tr>" for p in products
    ) or "<tr><td colspan=\"6\">No product data available.</td></tr>"
    customer_rows = "".join(
        f"<tr><td>{html.escape(str(c['customer_name']))}</td><td>{money(c['revenue'])}</td>"
        f"<td>{money(c['transport'])}</td><td>{money(c['contribution'])}</td><td>{c['margin_pct']:.1f}%</td></tr>"
        for c in customers
    ) or "<tr><td colspan=\"5\">No exploitable customer data.</td></tr>"
    transport_rows = "".join(
        f"<tr><td>{html.escape(str(r['customer_name']))}</td><td>{money(r['transport'])}</td>"
        f"<td>{(float(r['transport'] or 0) / float(r['revenue'] or 1) * 100):.1f}%</td></tr>"
        for r in data["transport"]
    ) or "<tr><td colspan=\"3\">Per-customer transport data unavailable.</td></tr>"
    expense_rows = "".join(
        f"<tr><td>{html.escape(str(e['expense_type']))}</td><td>{money(e['previous'])}</td>"
        f"<td>{money(e['current'])}</td>"
        f"<td class=\"{'down' if e['change'] >= 0 else 'up'}\">{money(e['change'])}</td>"
        f"<td>{percent(e['variation_pct'])}</td></tr>" for e in data["expenses"]
    ) or "<tr><td colspan=\"5\">Expense data unavailable.</td></tr>"
    receivable_rows = "".join(
        f"<tr><td>{html.escape(str(r['customer_name']))}</td><td>{money(r['outstanding_balance'])}</td>"
        f"<td>{int(r['days_overdue'] or 0)} days</td></tr>" for r in data["receivables"]
    ) or "<tr><td colspan=\"3\">No overdue receivables detected.</td></tr>"
    stock_rows = "".join(
        f"<tr><td>{html.escape(str(s['product_name']))}</td><td>{s['current_stock']:,.0f}</td>"
        f"<td>{s['reorder_point']:,.0f}</td><td>{html.escape(str(s['status']))}</td></tr>"
        for s in data["stock"]
    ) or "<tr><td colspan=\"4\">No stock alerts — all SKUs within normal range.</td></tr>"
    recommendation_items = "".join(f"<li>{html.escape(r)}</li>" for r in data["recommendations"])
    additional_data_items = "".join(f"<li>{html.escape(r)}</li>" for r in data["additional_data_needed"])

    proxy_warning = (
        "<div class=\"warning\"><strong>Contribution is an analytical estimate.</strong> "
        "Annual COGS is allocated pro-rata to revenue. This is not accounting-grade customer profitability "
        "and should only be used to prioritise a review." + " " + badge("ESTIMATED") + "</div>"
        if data["customer_is_proxy"] else
        f"<div class=\"note\">Direct cost figures were found on the sales rows for this computation. {badge('MEASURED')}</div>"
    )

    main_driver = data["main_driver"]
    driver_text = "Insufficient data to isolate a main driver."
    if main_driver:
        driver_text = f"{main_driver['label']}: estimated EBITDA impact of {money(main_driver['ebitda_impact'])}."

    quick_questions = "".join(
        f'<button type="button" class="btn btn-ghost" onclick="askQA({html.escape(json.dumps(q["text"]))})">{html.escape(q["label"])}</button>'
        for q in business_qa.CANONICAL_QUESTIONS
    )

    demo_banner = ""
    demo_nav = ""
    if demo_mode:
        demo_banner = (
            '<div class="demo-banner"><strong>Commercial demo mode.</strong> Walk the executive through '
            'this page top to bottom: what happened → why → where the problem is → what to do '
            '→ what additional data would sharpen the answer. Every figure is tagged '
            f'{badge("MEASURED")} {badge("ESTIMATED")} or {badge("NO DATA")} so nothing is presented as fact '
            'unless it is.</div>'
        )
        demo_nav = '<nav class="demo-nav">' + "".join(
            f'<a href="#s{i}">Step {i}</a>' for i in range(1, 10)
        ) + '</nav>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOUKAM Business Intelligence | CAMTRADE</title><style>{STYLE}</style></head>
<body><main class="wrap">
<header class="header"><div><div class="brand">TOUKAM BUSINESS INTELLIGENCE</div>
<h1>CAMTRADE Distribution</h1>
<div class="muted">AI-powered Business Intelligence for Finance &amp; Operations · fiscal year {data['year']} · illustrative data</div></div>
<div style="display:flex;gap:8px"><a class="btn btn-ghost" href="/{'' if demo_mode else 'demo'}">{'Exit demo mode' if demo_mode else 'Start commercial demo'}</a>
<button onclick="location.reload()">Refresh</button></div></header>
{demo_banner}{demo_nav}

<h2 id="s1">{step_tag(1, demo_mode)} Executive overview</h2>
{talking_point("This is the one screen a director needs before deciding whether to keep listening.", demo_mode)}
<section class="grid">
<div class="card"><div class="label">Revenue {badge('MEASURED')}</div><div class="value">{money(current['revenue'])}</div>
<div class="small {'up' if revenue_delta >= 0 else 'down'}">{money(revenue_delta)} vs {data['previous_year']}</div></div>
<div class="card"><div class="label">EBITDA (analytical) {badge('ESTIMATED')}</div><div class="value">{money(current['ebitda'])}</div>
<div class="small {'up' if ebitda_delta >= 0 else 'down'}">{money(ebitda_delta)} vs {data['previous_year']}</div></div>
<div class="card"><div class="label">EBITDA margin</div><div class="value">{current['ebitda_margin_pct']:.1f}%</div>
<div class="small">vs {previous['ebitda_margin_pct']:.1f}% in {data['previous_year']}</div></div>
<div class="card"><div class="label">Overdue receivables {badge('MEASURED')}</div><div class="value">{len(data['receivables'])}</div>
<div class="small">{money(data['receivables_total'])} to review</div></div>
</section>
<section class="panel"><div class="insight"><strong>Headline</strong><br>{html.escape(data['summary'])}</div>
<div class="insight"><strong>Priority explanatory factor</strong><br>{html.escape(driver_text)}</div>
<div class="note">EBITDA, margins and impacts are analytical indicators; they do not replace audited financial statements.</div>
</section>

<h2 id="s2">{step_tag(2, demo_mode)} Revenue vs. EBITDA, {data['previous_year']} → {data['year']}</h2>
{talking_point("Revenue moved one way, EBITDA moved the other — that gap is the whole story.", demo_mode)}
<section class="panel"><table><thead><tr><th>Indicator</th><th>{data['previous_year']}</th><th>{data['year']}</th><th>Variation</th></tr></thead>
<tbody>
<tr><td>Revenue {badge('MEASURED')}</td><td>{money(previous['revenue'])}</td><td>{money(current['revenue'])}</td><td>{percent(variation(previous['revenue'], current['revenue']))}</td></tr>
<tr><td>EBITDA (analytical) {badge('ESTIMATED')}</td><td>{money(previous['ebitda'])}</td><td>{money(current['ebitda'])}</td><td>{percent(variation(previous['ebitda'], current['ebitda']))}</td></tr>
</tbody></table></section>

<h2 id="s3">{step_tag(3, demo_mode)} EBITDA deterioration — bridge</h2>
{talking_point("Read this table top to bottom: it explains, line by line, where every FCFA of EBITDA change came from.", demo_mode)}
<section class="panel"><table><thead><tr><th>Component</th><th>{data['previous_year']}</th><th>{data['year']}</th><th>EBITDA impact</th><th>Variation</th></tr></thead>
<tbody>{bridge_rows}</tbody></table>
<div class="warning"><strong>COGS is an estimated proxy.</strong> Without opening/closing inventory and a documented costing rule, COGS is not a real accounting cost — it is a weighted-average purchase cost applied to units sold.</div>
</section>

<h2 id="s4">{step_tag(4, demo_mode)} Root causes</h2>
{talking_point("This is where the tool moves from 'what happened' to 'why' — grounded in the data, not a guess.", demo_mode)}
<section class="two">
<div><div class="panel"><table><thead><tr><th>Customer</th><th>Transport</th><th>% of revenue</th></tr></thead>
<tbody>{transport_rows}</tbody></table>
<div class="note">Transport measured directly from sales; cross-check with operations. {badge('MEASURED')}</div></div></div>
<div><div class="panel"><table><thead><tr><th>Expense category</th><th>{data['previous_year']}</th><th>{data['year']}</th><th>Change</th><th>Variation</th></tr></thead>
<tbody>{expense_rows}</tbody></table>
<div class="note">{html.escape(data['expense_method'])}. {badge('MEASURED')}</div></div></div>
</section>

<h2 id="s5">{step_tag(5, demo_mode)} Product profitability</h2>
{talking_point("Sorted worst margin first — these are the SKUs a pricing or sourcing review should start with.", demo_mode)}
<section class="panel"><table><thead><tr><th>Product</th><th>Units sold</th><th>Revenue</th><th>Allocated cost</th><th>Margin</th><th>Margin %</th></tr></thead>
<tbody>{product_rows}</tbody></table>
<div class="warning">{html.escape(data['product_method'])}. {badge('ESTIMATED')}</div></section>

<h2 id="s6">{step_tag(6, demo_mode)} Customer profitability</h2>
{talking_point("Same logic, applied to customers — least profitable first.", demo_mode)}
<section class="panel"><table><thead><tr><th>Customer</th><th>Revenue</th><th>Transport</th><th>Contribution*</th><th>Margin*</th></tr></thead>
<tbody>{customer_rows}</tbody></table>{proxy_warning}</section>

<h2 id="s7">{step_tag(7, demo_mode)} Receivables risk &amp; stock alerts</h2>
{talking_point("Two very different risks, both measured directly from the database — no modelling needed here.", demo_mode)}
<section class="two">
<div><h2 style="margin-top:0;font-size:14px">Receivables at risk {badge('MEASURED')}</h2><div class="panel"><table><thead><tr><th>Customer</th><th>Outstanding</th><th>Overdue</th></tr></thead>
<tbody>{receivable_rows}</tbody></table>
<div class="note">Validate against accounting records before any collection action.</div></div></div>
<div><h2 style="margin-top:0;font-size:14px">Stock alerts {badge('MEASURED')}</h2><div class="panel"><table><thead><tr><th>Product</th><th>Current stock</th><th>Reorder point</th><th>Status</th></tr></thead>
<tbody>{stock_rows}</tbody></table>
<div class="note">Read directly from the inventory table.</div></div></div>
</section>

<h2 id="s8">{step_tag(8, demo_mode)} Business Q&amp;A</h2>
{talking_point("Now let the executive ask their own question — this is the moment that sells the product.", demo_mode)}
<section class="panel">
<div class="chips">{quick_questions}</div>
<form id="qa-form" class="qa-form">
<input id="qa-question" type="text" placeholder="Ask a question about CAMTRADE's finances or operations…" autocomplete="off">
<button type="submit">Ask</button>
</form>
<div id="qa-result"><div class="note">Pick a question above, or type your own. Questions outside the current dataset (sales, purchases, expenses, customers, products, receivables, inventory) will be flagged {badge('NO DATA')} rather than answered with a guess.</div></div>
</section>

<h2 id="s9">{step_tag(9, demo_mode)} Management recommendations</h2>
{talking_point("Close on actions and on what more data would sharpen the picture — this is the ask.", demo_mode)}
<section class="two">
<div><div class="panel"><ol>{recommendation_items}</ol></div></div>
<div><div class="panel"><ul class="plain">{additional_data_items}</ul>
<div class="note">Additional data required to move estimates to measured facts.</div></div></div>
</section>

<footer class="footer">CAMTRADE demonstration · Toukam Business Intelligence · Illustrative data · Every estimate is explicitly labelled.</footer>
</main>
<script>{SCRIPT}</script>
</body></html>"""


# ----------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body_str: str, status: int = 200) -> None:
        payload = body_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                self._send_html(render(gather_all(YEAR), demo_mode=False))
            elif path == "/demo":
                self._send_html(render(gather_all(YEAR), demo_mode=True))
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            elif path == "/api/health":
                self._send_json({"status": "ok", "database": str(find_database())})
            elif path == "/api/qa":
                question = (query.get("q", [""])[0]).strip()
                try:
                    year = int(query.get("year", [str(YEAR)])[0])
                except ValueError:
                    year = YEAR
                if not question:
                    self._send_json({"error": "missing required query parameter 'q'"}, status=400)
                    return
                self._send_json(business_qa.answer(question, year=year))
            else:
                self.send_error(404)
        except Exception as exc:  # visible, never silently replaced by fake metrics
            if path.startswith("/api/"):
                self._send_json({"error": str(exc)}, status=500)
            else:
                self._send_html(
                    f"<h1>CAMTRADE — error</h1><pre>{html.escape(str(exc))}</pre>", status=500
                )

    def log_message(self, format: str, *args: object) -> None:
        print(f"[CAMTRADE] {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    # The URL printed/opened locally always says 127.0.0.1 (what Romeo should
    # type/click on his own machine) even though the socket binds 0.0.0.0 on
    # a host -- on a host, the platform's own public URL is what visitors
    # use, never this printed one, and it never opens a local browser there
    # (no display to open one on, and _RUNNING_ON_A_HOST already says so).
    local_url = f"http://127.0.0.1:{PORT}"
    print(f"TOUKAM Business Intelligence — CAMTRADE dashboard: {local_url}")
    print(f"Commercial demo path: {local_url}/demo")
    print("Stop: Ctrl+C")
    if not _RUNNING_ON_A_HOST:
        threading.Timer(0.6, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

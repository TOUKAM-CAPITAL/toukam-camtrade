#!/usr/bin/env python3
"""CAMTRADE — Decision Engine (commercial demo edition).

Produces a transparent executive diagnostic from a CAMTRADE SQLite database.
The engine intentionally distinguishes measured facts from analytical estimates.
In particular, a customer contribution based on annual purchase allocation is
never presented as accounting profitability or as a reason to drop a customer.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


TITLE = "TOUKAM BUSINESS INTELLIGENCE — CAMTRADE"
DEFAULT_YEAR = 2025
KNOWN_TABLES = {"sales", "purchases", "expenses", "customers", "products", "receivables"}


def find_database(explicit_path: str | None = None) -> Path:
    """Return the first existing database candidate, with a useful error if absent."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    if os.getenv("CAMTRADE_DB"):
        candidates.append(Path(os.environ["CAMTRADE_DB"]).expanduser())

    base = Path(__file__).resolve().parent
    candidates.extend((
        base / "CAMTRADE_DATA" / "CAMTRADE.db",
        base.parent / "CAMTRADE_DATA" / "CAMTRADE.db",
        Path.cwd() / "CAMTRADE_DATA" / "CAMTRADE.db",
        Path.cwd() / "CAMTRADE.db",
    ))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    locations = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Base CAMTRADE introuvable. Placez-la dans CAMTRADE_DATA/CAMTRADE.db "
        "à côté de ce fichier, ou lancez : python decision_engine.py --db CHEMIN\n"
        f"Emplacements vérifiés :\n  - {locations}"
    )


class CamtradeData:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._tables = self._load_tables()
        self._columns: dict[str, set[str]] = {}

    def close(self) -> None:
        self.conn.close()

    def _load_tables(self) -> set[str]:
        return {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}

    def has_table(self, table: str) -> bool:
        return table in self._tables

    def columns(self, table: str) -> set[str]:
        if table not in self._columns:
            if not self.has_table(table):
                return set()
            self._columns[table] = {
                row["name"] for row in self.conn.execute(f'PRAGMA table_info("{table}")')
            }
        return self._columns[table]

    def first_column(self, table: str, names: Iterable[str]) -> str | None:
        available = self.columns(table)
        return next((name for name in names if name in available), None)

    def scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> float:
        value = self.conn.execute(sql, parameters).fetchone()[0]
        return float(value or 0)

    def rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(sql, parameters).fetchall()]


def amount(value: float) -> str:
    return f"{value:,.0f} FCFA"


def pct(value: float | None) -> str:
    return "n.d." if value is None else f"{value:.1f}%"


def change(old: float, new: float) -> float | None:
    return None if old == 0 else (new - old) / abs(old) * 100


def year_filter(date_column: str) -> str:
    return f"strftime('%Y', {date_column}) = ?"


def cogs_proxy_from_sales(data: CamtradeData, year: int) -> dict[str, Any]:
    """Estimate COGS from units sold and product-level purchase data.

    CAMTRADE has no opening inventory, lot traceability or stock valuation rule.
    The output is therefore a CMUP proxy, never accounting COGS: annual weighted
    purchase cost and freight per product are applied to units actually sold.
    """
    if not {"product_id", "quantity"}.issubset(data.columns("sales")):
        return {"cogs": 0.0, "product_cost": 0.0, "inbound_freight": 0.0,
                "method": "indisponible : produit/quantité absents des ventes", "is_proxy": True}
    sales_date = data.first_column("sales", ("sale_date", "date", "invoice_date"))
    purchase_date = data.first_column("purchases", ("purchase_date", "date", "invoice_date"))
    if not sales_date:
        return {"cogs": 0.0, "product_cost": 0.0, "inbound_freight": 0.0,
                "method": "indisponible : date absente des ventes", "is_proxy": True}
    reference_cost = "p.purchase_cost" if "purchase_cost" in data.columns("products") else "NULL"
    if purchase_date and {"product_id", "quantity", "total_cost"}.issubset(data.columns("purchases")):
        freight_expr = "SUM(COALESCE(x.freight, 0))" if "freight" in data.columns("purchases") else "0"
        rows = data.rows(f"""
            WITH purchase_costs AS (
                SELECT x.product_id, SUM(x.total_cost) / NULLIF(SUM(x.quantity), 0) AS weighted_unit_cost,
                       {freight_expr} / NULLIF(SUM(x.quantity), 0) AS freight_per_unit
                FROM purchases x WHERE {year_filter('x.' + purchase_date)} GROUP BY x.product_id
            )
            SELECT s.product_id, SUM(s.quantity) AS units_sold,
                   COALESCE(pc.weighted_unit_cost, {reference_cost}) AS unit_cost,
                   COALESCE(pc.freight_per_unit, 0) AS freight_per_unit
            FROM sales s LEFT JOIN purchase_costs pc ON pc.product_id = s.product_id
            LEFT JOIN products p ON p.product_id = s.product_id
            WHERE {year_filter('s.' + sales_date)} GROUP BY s.product_id
        """, (str(year), str(year)))
        method = "proxy CMUP : coût d'achat et fret moyens pondérés par produit, appliqués aux quantités vendues"
    else:
        rows = data.rows(f"""
            SELECT s.product_id, SUM(s.quantity) AS units_sold, {reference_cost} AS unit_cost,
                   0 AS freight_per_unit FROM sales s LEFT JOIN products p ON p.product_id = s.product_id
            WHERE {year_filter('s.' + sales_date)} GROUP BY s.product_id
        """, (str(year),))
        method = "proxy de secours : coût de référence produit appliqué aux quantités vendues; fret non alloué"
    product_cost = sum(float(row["units_sold"] or 0) * float(row["unit_cost"] or 0) for row in rows)
    inbound_freight = sum(float(row["units_sold"] or 0) * float(row["freight_per_unit"] or 0) for row in rows)
    return {"cogs": product_cost + inbound_freight, "product_cost": product_cost,
            "inbound_freight": inbound_freight, "method": method, "is_proxy": True}


def annual_performance(data: CamtradeData, year: int) -> dict[str, Any]:
    """Collect annual inputs; raw annual purchases are not treated as COGS."""
    def yearly_sum(table: str, value_names: tuple[str, ...], date_names: tuple[str, ...]) -> float:
        value_col = data.first_column(table, value_names)
        date_col = data.first_column(table, date_names)
        if not value_col or not date_col:
            return 0.0
        return data.scalar(
            f"SELECT COALESCE(SUM({value_col}), 0) FROM {table} WHERE {year_filter(date_col)}",
            (str(year),),
        )

    cogs = cogs_proxy_from_sales(data, year)
    return {
        "year": float(year),
        "revenue": yearly_sum("sales", ("revenue", "amount", "sales_amount", "total"), ("sale_date", "date", "invoice_date")),
        "purchases": yearly_sum("purchases", ("total_cost", "amount", "purchase_cost", "total"), ("purchase_date", "date", "invoice_date")),
        "purchase_freight": yearly_sum("purchases", ("freight", "transport", "shipping_cost"), ("purchase_date", "date", "invoice_date")),
        "sales_transport": yearly_sum("sales", ("allocated_transport", "transport", "delivery_cost"), ("sale_date", "date", "invoice_date")),
        "operating_expenses": yearly_sum("expenses", ("amount", "expense_amount", "total"), ("expense_month", "expense_date", "date", "month")),
        "cogs": cogs["cogs"], "cogs_product_cost": cogs["product_cost"],
        "cogs_inbound_freight": cogs["inbound_freight"], "cogs_method": cogs["method"],
        "cogs_is_proxy": cogs["is_proxy"],
    }


def ebitda_analytical(inputs: dict[str, Any]) -> dict[str, Any]:
    revenue = inputs["revenue"]
    gross_profit = revenue - inputs["cogs"] - inputs["sales_transport"]
    ebitda = gross_profit - inputs["operating_expenses"]
    return {**inputs, "gross_profit": gross_profit, "ebitda": ebitda,
            "gross_margin_pct": gross_profit / revenue * 100 if revenue else 0.0,
            "ebitda_margin_pct": ebitda / revenue * 100 if revenue else 0.0}


def customer_contributions(data: CamtradeData, year: int) -> tuple[list[dict[str, Any]], str]:
    """Return customer analysis and the precise evidence level used."""
    required = ("customer_id", "revenue")
    sales_date = data.first_column("sales", ("sale_date", "date", "invoice_date"))
    if not sales_date or not all(c in data.columns("sales") for c in required):
        return [], "indisponible"

    cost_col = data.first_column("sales", ("cost", "total_cost", "cost_amount", "purchase_cost", "product_cost", "cost_of_goods"))
    transport_col = data.first_column("sales", ("allocated_transport", "transport", "delivery_cost"))
    customer_name = "c.customer_name" if "customer_name" in data.columns("customers") else "s.customer_id"
    join = "LEFT JOIN customers c ON c.customer_id = s.customer_id" if data.has_table("customers") and "customer_id" in data.columns("customers") else ""
    transport_expr = f"COALESCE(SUM(s.{transport_col}), 0)" if transport_col else "0"

    if cost_col:
        rows = data.rows(f"""
            SELECT s.customer_id, COALESCE({customer_name}, s.customer_id) AS customer_name,
                   COALESCE(SUM(s.revenue), 0) AS revenue,
                   COALESCE(SUM(s.{cost_col}), 0) AS direct_cost,
                   {transport_expr} AS transport
            FROM sales s {join}
            WHERE {year_filter('s.' + sales_date)}
            GROUP BY s.customer_id, customer_name
        """, (str(year),))
        method = "coûts directs présents dans les lignes de vente"
    else:
        rows = data.rows(f"""
            SELECT s.customer_id, COALESCE({customer_name}, s.customer_id) AS customer_name,
                   COALESCE(SUM(s.revenue), 0) AS revenue, {transport_expr} AS transport
            FROM sales s {join}
            WHERE {year_filter('s.' + sales_date)}
            GROUP BY s.customer_id, customer_name
        """, (str(year),))
        total_revenue = sum(float(row["revenue"] or 0) for row in rows)
        cogs_total = annual_performance(data, year)["cogs"]
        for row in rows:
            row["direct_cost"] = cogs_total * float(row["revenue"] or 0) / total_revenue if total_revenue else 0.0
        method = "proxy : COGS estimé réparti au prorata du chiffre d'affaires"

    result: list[dict[str, Any]] = []
    for row in rows:
        revenue = float(row["revenue"] or 0)
        cost = float(row.get("direct_cost") or 0)
        transport = float(row["transport"] or 0)
        contribution = revenue - cost - transport
        result.append({**row, "revenue": revenue, "direct_cost": cost, "transport": transport,
                       "contribution": contribution, "margin_pct": contribution / revenue * 100 if revenue else 0.0})
    return sorted(result, key=lambda row: row["contribution"]), method


def transport_analysis(data: CamtradeData, year: int) -> list[dict[str, Any]]:
    sales_date = data.first_column("sales", ("sale_date", "date", "invoice_date"))
    transport = data.first_column("sales", ("allocated_transport", "transport", "delivery_cost"))
    if not sales_date or not transport or "customer_id" not in data.columns("sales"):
        return []
    name = "c.customer_name" if "customer_name" in data.columns("customers") else "s.customer_id"
    join = "LEFT JOIN customers c ON c.customer_id=s.customer_id" if data.has_table("customers") and "customer_id" in data.columns("customers") else ""
    return data.rows(f"""
        SELECT s.customer_id, COALESCE({name}, s.customer_id) AS customer_name,
               SUM(s.{transport}) AS transport, SUM(s.revenue) AS revenue
        FROM sales s {join} WHERE {year_filter('s.' + sales_date)}
        GROUP BY s.customer_id, customer_name ORDER BY transport DESC LIMIT 5
    """, (str(year),))


def credit_risk(data: CamtradeData, limit: int = 5) -> list[dict[str, Any]]:
    if not data.has_table("receivables"):
        return []
    needed = {"customer_id", "outstanding_balance", "days_overdue"}
    if not needed.issubset(data.columns("receivables")):
        return []
    name = "customer_name" if "customer_name" in data.columns("receivables") else "customer_id"
    status_filter = "WHERE status = 'En retard'" if "status" in data.columns("receivables") else "WHERE days_overdue > 0"
    return data.rows(f"SELECT customer_id, {name} AS customer_name, outstanding_balance, days_overdue "
                     f"FROM receivables {status_filter} ORDER BY outstanding_balance DESC LIMIT {int(limit)}")


def product_margin_analysis(data: CamtradeData, year: int) -> tuple[list[dict[str, Any]], str, bool]:
    """Per-product revenue vs. allocated cost, to flag which products destroy the most margin.

    Uses the same weighted-average-purchase-cost-per-unit proxy as the COGS engine
    (cogs_proxy_from_sales), applied at product granularity instead of totals.
    This is always an analytical estimate: CAMTRADE has no per-sale product cost.
    """
    sales_date = data.first_column("sales", ("sale_date", "date", "invoice_date"))
    if not sales_date or not {"product_id", "quantity", "revenue"}.issubset(data.columns("sales")):
        return [], "unavailable: sales table must include product_id, quantity and revenue", True

    purchase_date = data.first_column("purchases", ("purchase_date", "date", "invoice_date"))
    product_name_col = "p.product_name" if "product_name" in data.columns("products") else "s.product_id"
    join_products = "LEFT JOIN products p ON p.product_id = s.product_id" if data.has_table("products") else ""
    reference_cost = "p.purchase_cost" if "purchase_cost" in data.columns("products") else "NULL"

    cost_by_product: dict[str, dict[str, Any]] = {}
    if purchase_date and {"product_id", "quantity", "total_cost"}.issubset(data.columns("purchases")):
        freight_expr = "SUM(x.freight)" if "freight" in data.columns("purchases") else "0"
        cost_rows = data.rows(f"""
            SELECT x.product_id, SUM(x.total_cost) / NULLIF(SUM(x.quantity), 0) AS weighted_unit_cost,
                   {freight_expr} / NULLIF(SUM(x.quantity), 0) AS freight_per_unit
            FROM purchases x WHERE {year_filter('x.' + purchase_date)} GROUP BY x.product_id
        """, (str(year),))
        cost_by_product = {row["product_id"]: row for row in cost_rows}
        method = "proxy: weighted-average purchase cost + freight per unit (from purchases in the period), applied to units sold"
    else:
        method = "proxy: reference product cost only (no purchase-level costing available for the period)"

    rows = data.rows(f"""
        SELECT s.product_id, COALESCE({product_name_col}, s.product_id) AS product_name,
               SUM(s.quantity) AS units_sold, SUM(s.revenue) AS revenue,
               COALESCE({reference_cost}, 0) AS reference_cost
        FROM sales s {join_products}
        WHERE {year_filter('s.' + sales_date)}
        GROUP BY s.product_id, product_name
    """, (str(year),))

    result: list[dict[str, Any]] = []
    for row in rows:
        units = float(row["units_sold"] or 0)
        revenue = float(row["revenue"] or 0)
        proxy = cost_by_product.get(row["product_id"])
        if proxy and proxy.get("weighted_unit_cost") is not None:
            unit_cost = float(proxy["weighted_unit_cost"] or 0)
            freight_unit = float(proxy["freight_per_unit"] or 0)
        else:
            unit_cost = float(row["reference_cost"] or 0)
            freight_unit = 0.0
        allocated_cost = units * (unit_cost + freight_unit)
        margin = revenue - allocated_cost
        result.append({
            "product_id": row["product_id"], "product_name": row["product_name"],
            "units_sold": units, "revenue": revenue, "allocated_cost": allocated_cost,
            "margin": margin, "margin_pct": (margin / revenue * 100) if revenue else 0.0,
        })
    return sorted(result, key=lambda r: r["margin"]), method, True


def expense_variance(data: CamtradeData, year: int, previous_year: int) -> tuple[list[dict[str, Any]], str]:
    """Compare operating expenses by category across two years — measured directly
    from the expenses table (no allocation or estimation involved)."""
    if not data.has_table("expenses"):
        return [], "unavailable: expenses table missing"
    type_col = data.first_column("expenses", ("expense_type", "category", "type"))
    amount_col = data.first_column("expenses", ("amount", "expense_amount", "total"))
    date_col = data.first_column("expenses", ("expense_month", "expense_date", "date", "month"))
    if not (type_col and amount_col and date_col):
        return [], "unavailable: expenses table missing type/amount/date columns"

    def totals_for(target_year: int) -> dict[str, float]:
        rows = data.rows(
            f"SELECT {type_col} AS expense_type, SUM({amount_col}) AS total FROM expenses "
            f"WHERE {year_filter(date_col)} GROUP BY expense_type",
            (str(target_year),),
        )
        return {row["expense_type"]: float(row["total"] or 0) for row in rows}

    current_totals, previous_totals = totals_for(year), totals_for(previous_year)
    categories = sorted(set(current_totals) | set(previous_totals))
    result = []
    for category in categories:
        old, new = previous_totals.get(category, 0.0), current_totals.get(category, 0.0)
        result.append({
            "expense_type": category, "previous": old, "current": new,
            "change": new - old, "variation_pct": change(old, new),
        })
    return sorted(result, key=lambda r: r["change"], reverse=True), "measured: summed directly from the expenses table by category and period"


def line(char: str = "=") -> None:
    print(char * 84)


def diagnose(data: CamtradeData, year: int) -> None:
    current, previous = ebitda_analytical(annual_performance(data, year)), ebitda_analytical(annual_performance(data, year - 1))
    customers, customer_method = customer_contributions(data, year)
    transport, receivables = transport_analysis(data, year), credit_risk(data)

    line(); print(f"{TITLE} — RAPPORT EXÉCUTIF {year}"); line()
    print(f"Base analysée : {data.db_path}")
    print("Important : EBITDA et marges ci-dessous sont des indicateurs analytiques, pas des états financiers audités.")

    print("\n1. PERFORMANCE 2024 / 2025")
    line("-")
    print(f"{'Indicateur':<30}{year - 1:>18}{year:>18}{'Variation':>16}")
    for label, key in (("Chiffre d'affaires", "revenue"), ("COGS estimé", "cogs"), ("Marge brute analytique", "gross_profit"), ("EBITDA analytique", "ebitda")):
        print(f"{label:<30}{amount(previous[key]):>18}{amount(current[key]):>18}{pct(change(previous[key], current[key])):>16}")
    print(f"Marge EBITDA analytique : {pct(previous['ebitda_margin_pct'])} -> {pct(current['ebitda_margin_pct'])}")

    print("\n2. DIAGNOSTIC EBITDA — FACTEURS EXPLICATIFS")
    line("-")
    components = (("CA", "revenue", 1), ("COGS estimé — coût produit", "cogs_product_cost", -1), ("COGS estimé — fret entrant", "cogs_inbound_freight", -1),
                  ("Transport des ventes", "sales_transport", -1), ("Dépenses opérationnelles", "operating_expenses", -1))
    for label, key, sign in components:
        raw_delta = current[key] - previous[key]
        impact = raw_delta * sign
        direction = "améliore" if impact >= 0 else "dégrade"
        print(f"• {label:<28} {amount(current[key]):>18} | évolution {amount(raw_delta):>18} | {direction} l'EBITDA de {amount(abs(impact))}")
    print(f"Méthode COGS : {current['cogs_method']}.")
    print("ATTENTION : ce COGS est un proxy ; sans stocks d'ouverture/clôture et règle FIFO/CMUP documentée, il ne constitue pas un coût comptable réel.")

    print("\n3. ANALYSE CLIENTS")
    line("-")
    if not customers:
        print("Analyse client indisponible : sales doit contenir customer_id, revenue et une date de vente.")
    else:
        print(f"Méthode : {customer_method}.")
        if customer_method.startswith("proxy"):
            print("ATTENTION — Estimation de contribution : modèle analytique, PAS une rentabilité client comptable.")
            print("  Valider prix, produits, stock et coûts directs avant décision : la clé client n'est pas disponible pour affecter le COGS directement.")
        print(f"{'Client':<24}{'CA':>16}{'Transport':>16}{'Contribution*':>18}{'Marge*':>10}")
        for row in customers[:5]:
            print(f"{str(row['customer_name'])[:23]:<24}{amount(row['revenue']):>16}{amount(row['transport']):>16}{amount(row['contribution']):>18}{pct(row['margin_pct']):>10}")
        print("* contribution/marge selon la méthode indiquée ; à utiliser pour prioriser une vérification, pas pour conclure seul.")

    print("\n4. TRANSPORT ET RISQUE DE CRÉDIT")
    line("-")
    if transport:
        print("Clients concentrant le plus de transport mesuré :")
        for row in transport:
            revenue = float(row["revenue"] or 0); cost = float(row["transport"] or 0)
            print(f"• {row['customer_name']}: {amount(cost)} ({pct(cost / revenue * 100 if revenue else 0)} du CA)")
    else:
        print("Transport par client indisponible dans les données de vente.")
    if receivables:
        print("Créances en retard à examiner :")
        for row in receivables:
            print(f"• {row['customer_name']}: {amount(float(row['outstanding_balance'] or 0))}, {int(row['days_overdue'] or 0)} jours de retard")
    else:
        print("Aucune analyse de créances disponible (table ou colonnes absentes).")

    print("\n5. RECOMMANDATIONS PRIORITAIRES")
    line("-")
    recommendations: list[str] = []
    if change(previous["ebitda"], current["ebitda"]) is not None and current["ebitda"] < previous["ebitda"]:
        recommendations.append("Réconcilier la baisse d'EBITDA avec les variations de CA, achats, fret, transport et dépenses avant toute action commerciale.")
    if customer_method.startswith("proxy"):
        recommendations.append("Construire un coût des marchandises vendues par client/vente ; ne pas négocier ou abandonner un client sur la seule répartition analytique du COGS.")
    if transport:
        recommendations.append("Revoir les tournées, minimums de commande et conditions de livraison des clients aux coûts de transport les plus élevés.")
    if receivables:
        recommendations.append("Prioriser le recouvrement des créances en retard et fixer une revue hebdomadaire des encours à risque.")
    recommendations.append("Valider ce diagnostic avec finance/contrôle de gestion, puis lancer un plan d'action avec responsables et dates de revue.")
    for index, recommendation in enumerate(recommendations, 1):
        print(f"{index}. {recommendation}")

    print("\n6. RÉSUMÉ EXÉCUTIF")
    line("-")
    revenue_direction = "progresse" if current["revenue"] >= previous["revenue"] else "recule"
    ebitda_direction = "progresse" if current["ebitda"] >= previous["ebitda"] else "recule"
    print(f"En {year}, le chiffre d'affaires {revenue_direction} à {amount(current['revenue'])} et l'EBITDA analytique {ebitda_direction} à {amount(current['ebitda'])}.")
    print("La priorité est de convertir les signaux de ce rapport en vérifications factuelles : coût des produits vendus, stock, transport et créances.")
    line(); print("CAMTRADE — démonstration décisionnelle prête. Toute estimation est explicitement signalée."); line()


def main() -> int:
    parser = argparse.ArgumentParser(description="CAMTRADE Decision Engine")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help="année analysée (défaut : 2025)")
    parser.add_argument("--db", help="chemin vers CAMTRADE.db")
    args = parser.parse_args()
    try:
        data = CamtradeData(find_database(args.db))
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    try:
        if not data.has_table("sales"):
            print("ERREUR : la table 'sales' est absente de la base CAMTRADE.", file=sys.stderr)
            return 2
        diagnose(data, args.year)
        return 0
    except sqlite3.Error as exc:
        print(f"ERREUR DE LECTURE SQLITE : {exc}", file=sys.stderr)
        return 2
    finally:
        data.close()


if __name__ == "__main__":
    raise SystemExit(main())

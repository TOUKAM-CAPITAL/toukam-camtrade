import sqlite3
from pathlib import Path

from decision_engine import (
    CamtradeData,
    annual_performance as decision_annual_performance,
    find_database,
)

# ============================================================
# TOUKAM BUSINESS INTELLIGENCE
# CAMTRADE - ANALYTICS ENGINE v1
# ============================================================

BASE = Path(__file__).resolve().parent
DB = BASE / "CAMTRADE_DATA" / "CAMTRADE.db"


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def query_one(sql, params=()):
    conn = connect()
    result = conn.execute(sql, params).fetchone()
    conn.close()

    if result is None:
        return {}

    return dict(result)


def annual_performance(year):
    """Use the same financial inputs and COGS method as Decision Engine."""
    data = CamtradeData(find_database())
    try:
        result = decision_annual_performance(data, year)
        # Legacy aliases retained for callers that monitor procurement spend.
        # They are not used to calculate COGS, gross margin or EBITDA.
        result["purchase_cost"] = result["purchases"]
        return result
    finally:
        data.close()


def calculate_ebitda(performance):
    revenue = performance["revenue"]
    cogs = performance["cogs"]
    sales_transport = performance["sales_transport"]
    operating_expenses = performance["operating_expenses"]

    gross_profit = (
        revenue
        - cogs
        - sales_transport
    )

    ebitda = gross_profit - operating_expenses

    return {
        **performance,

        # Noms utilisés par le Decision Engine
        "gross_profit": gross_profit,
        "ebitda": ebitda,

        # Alias conservés pour compatibilité
        "gross_profit_proxy": gross_profit,
        "ebitda_proxy": ebitda,

        "cogs_proxy": cogs,

        "gross_margin_pct": (
            gross_profit / revenue * 100
            if revenue else 0
        ),

        "ebitda_margin_pct": (
            ebitda / revenue * 100
            if revenue else 0
        ),
    }


def compare_years(year1, year2):
    a = calculate_ebitda(annual_performance(year1))
    b = calculate_ebitda(annual_performance(year2))

    def variation(old, new):
        if old == 0:
            return 0
        return (new - old) / abs(old) * 100

    return {
        "year1": a,
        "year2": b,

        "revenue_change_pct": variation(
            a["revenue"], b["revenue"]
        ),

        "purchase_cost_change_pct": variation(
            a["purchase_cost"], b["purchase_cost"]
        ),

        "operating_expenses_change_pct": variation(
            a["operating_expenses"],
            b["operating_expenses"],
        ),

        "ebitda_change_pct": variation(
            a["ebitda"],
            b["ebitda"],
        ),

        "gross_margin_change_points":
            b["gross_margin_pct"] - a["gross_margin_pct"],

        "ebitda_margin_change_points":
            b["ebitda_margin_pct"] - a["ebitda_margin_pct"],
    }


def top_products_by_revenue(year):
    conn = connect()

    rows = conn.execute(
        """
        SELECT
            p.product_id,
            p.product_name,
            p.category,
            SUM(s.quantity) AS units_sold,
            SUM(s.revenue) AS revenue
        FROM sales s
        JOIN products p
            ON p.product_id = s.product_id
        WHERE strftime('%Y', s.sale_date) = ?
        GROUP BY p.product_id
        ORDER BY revenue DESC
        LIMIT 10
        """,
        (str(year),),
    ).fetchall()

    conn.close()
    return rows


def top_customers_by_revenue(year):
    conn = connect()

    rows = conn.execute(
        """
        SELECT
            c.customer_id,
            c.customer_name,
            c.city,
            c.segment,
            SUM(s.revenue) AS revenue
        FROM sales s
        JOIN customers c
            ON c.customer_id = s.customer_id
        WHERE strftime('%Y', s.sale_date) = ?
        GROUP BY c.customer_id
        ORDER BY revenue DESC
        LIMIT 10
        """,
        (str(year),),
    ).fetchall()

    conn.close()
    return rows


def stock_alerts():
    conn = connect()

    rows = conn.execute(
        """
        SELECT
            product_id,
            product_name,
            current_stock,
            reorder_point,
            average_monthly_demand,
            status
        FROM inventory
        WHERE status != 'Normal'
        ORDER BY status, current_stock ASC
        """
    ).fetchall()

    conn.close()
    return rows


def receivables_risk():
    conn = connect()

    rows = conn.execute(
        """
        SELECT
            customer_id,
            customer_name,
            outstanding_balance,
            days_overdue,
            status
        FROM receivables
        WHERE status = 'En retard'
        ORDER BY outstanding_balance DESC
        """
    ).fetchall()

    conn.close()
    return rows


# ============================================================
# TEST DU MOTEUR
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("TOUKAM BUSINESS INTELLIGENCE")
    print("CAMTRADE - ANALYTICS ENGINE")
    print("=" * 60)

    comparison = compare_years(2024, 2025)

    a = comparison["year1"]
    b = comparison["year2"]

    print("\nPERFORMANCE FINANCIERE")
    print("-" * 60)

    print(f"CA 2024       : {a['revenue']:,.0f} FCFA")
    print(f"CA 2025       : {b['revenue']:,.0f} FCFA")
    print(f"Variation CA  : {comparison['revenue_change_pct']:.2f}%")

    print()

    print(f"COGS proxy 2024   : {a['cogs']:,.0f} FCFA")
    print(f"COGS proxy 2025   : {b['cogs']:,.0f} FCFA")
    print(
        f"Variation achats (approvisionnement) : "
        f"{comparison['purchase_cost_change_pct']:.2f}%"
    )

    print()

    print(f"EBITDA 2024   : {a['ebitda']:,.0f} FCFA")
    print(f"EBITDA 2025   : {b['ebitda']:,.0f} FCFA")
    print(
        f"Variation EBITDA : "
        f"{comparison['ebitda_change_pct']:.2f}%"
    )

    print()

    print(
        f"Marge brute 2024 : "
        f"{a['gross_margin_pct']:.2f}%"
    )

    print(
        f"Marge brute 2025 : "
        f"{b['gross_margin_pct']:.2f}%"
    )

    print(
        f"Evolution marge brute : "
        f"{comparison['gross_margin_change_points']:.2f} points"
    )

    print()

    print(
        f"Marge EBITDA 2024 : "
        f"{a['ebitda_margin_pct']:.2f}%"
    )

    print(
        f"Marge EBITDA 2025 : "
        f"{b['ebitda_margin_pct']:.2f}%"
    )

    print(
        f"Evolution marge EBITDA : "
        f"{comparison['ebitda_margin_change_points']:.2f} points"
    )

    print("\nTOP 10 PRODUITS PAR CA — 2025")
    print("-" * 60)

    for row in top_products_by_revenue(2025):
        print(
            f"{row['product_name']:<25} "
            f"{row['revenue']:>15,.0f} FCFA"
        )

    print("\nALERTES STOCK")
    print("-" * 60)

    for row in stock_alerts():
        print(
            f"{row['product_name']:<25} "
            f"{row['status']}"
        )

    print("\nRISQUE CLIENTS / CREANCES")
    print("-" * 60)

    for row in receivables_risk()[:10]:
        print(
            f"{row['customer_name']:<15} "
            f"{row['outstanding_balance']:>15,.0f} FCFA "
            f"| {row['days_overdue']} jours"
        )

    print("\n" + "=" * 60)
    print("ANALYTICS ENGINE : TEST TERMINE")
    print("=" * 60)

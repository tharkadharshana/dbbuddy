"""
report_cache/registry.py — declarative catalog of the SalesPlay report APIs.

Single source of truth for: endpoint path, response shape (scalar vs
dimensional), storage grain, and per-metric aggregability. The core 8 are
fully declared below; the remaining reports are added as they're wired up —
adding a report here is ALL that's needed for it to become fetchable,
cacheable and tool-visible.

Field names are NOT guessed — they were read from the Laravel controllers in
docs/salesplay-internal-api-v2/app/Http/Controllers/App/Reports/ (each
report's comment cites its controller).

Metric.agg semantics (the additivity trap):
  sum          — safe to SUM across cached days/months (gross_sales, counts…)
  ratio        — must be recomputed from summed num/den, never averaged
                 (avg_ticket = SUM(total_sum) / SUM(receipt_count))
  non_additive — distinct counts / unit-level values / whole-range figures.
                 Summing cached period values gives a wrong-but-plausible
                 answer — the exact requested range must be fetched live.

Report.grain:
  'day'   — the report's table_data is already GROUP BY DATE server-side
            (only sales_summary), so one month-range call yields per-day facts.
  'month' — cached one calendar month at a time: scalar reports store the
            data.summary block, dimensional reports store table_data rows.

Report.endpoint is relative to SALESPLAY_EMBED_PROXY_BASE, which already ends
in "/app" — do NOT repeat it (would build .../app/app/sales_summary).
"""

from dataclasses import dataclass
from typing import Literal, Optional

Agg = Literal["sum", "ratio", "non_additive"]


@dataclass(frozen=True)
class Metric:
    key: str                    # normalized metric key, e.g. "net_sales"
    label: str
    agg: Agg
    num: Optional[str] = None   # ratio: numerator metric key
    den: Optional[str] = None   # ratio: denominator metric key
    aliases: tuple = ()         # alternate raw API keys for the same value
                                # (sales_summary table_data says "tips_amount",
                                # its summary block says "tips")


@dataclass(frozen=True)
class Report:
    id: str
    title: str
    description: str            # what business question it answers (routing)
    endpoint: str
    kind: Literal["scalar", "dimensional"]
    grain: Literal["day", "month"]
    metrics: tuple              # tuple[Metric, ...]
    answers: tuple              # keyword hints for tool routing
    dim_fields: tuple = ()      # dimensional: table_data field(s) forming the key
    extra_params: tuple = ()    # accepted params beyond the common set
    cacheable: bool = True      # False for point-in-time reports (stock levels,
                                # inventory valuation) where a "June summary"
                                # is meaningless — always fetched live

    def metric(self, key: str) -> Optional[Metric]:
        for m in self.metrics:
            if m.key == key:
                return m
        return None


# BaseReportRequest rules — accepted by every report endpoint.
COMMON_PARAMS = (
    "start_date", "end_date", "from_time", "to_time",
    "shop_id", "customer_id", "cashier_id", "page", "per_page", "search",
)

# The 8 reports fetched at onboarding (most-used, per product analytics).
CORE_REPORTS = (
    "sales_summary", "receipts", "refunds", "credit_notes",
    "taxes", "charges", "sales_by_products", "sales_by_category",
)

REPORTS: dict = {

    # SalesSummaryController@index — table_data is GROUP BY DATE already
    # (getMainSalesData SQL); per-day field names from formatTableDataNumbers().
    # operating_expenses/operating_profit/net_profit exist only in the
    # whole-range summary block (calculateSummaryTotals) — non_additive.
    "sales_summary": Report(
        id="sales_summary",
        title="Sales Summary",
        description="Daily gross/net sales, refunds, discounts, taxes, charges, gross profit",
        endpoint="/sales_summary",
        kind="scalar",
        grain="day",
        metrics=(
            Metric("gross_sales", "Gross sales", "sum"),
            Metric("refunds", "Refunds", "sum"),
            Metric("discount", "Discount", "sum"),
            Metric("net_sales", "Net sales", "sum"),
            Metric("taxes", "Taxes", "sum"),
            Metric("charges", "Charges", "sum"),
            Metric("product_cost", "Product cost", "sum"),
            Metric("gross_profit", "Gross profit", "sum"),
            Metric("tips_amount", "Tips", "sum", aliases=("tips",)),
            Metric("surcharge_amount", "Surcharge", "sum", aliases=("surcharge",)),
            Metric("gross_margin_pct", "Gross margin %", "ratio", num="gross_profit", den="net_sales"),
            Metric("operating_expenses", "Operating expenses", "non_additive"),
            Metric("operating_profit", "Operating profit", "non_additive"),
            Metric("net_profit", "Net profit", "non_additive"),
        ),
        answers=("sales", "revenue", "gross sales", "net sales", "profit", "discount", "daily sales"),
    ),

    # ReceiptsController@index — data.summary keys confirmed in controller
    # (empty-payload shape lines ~485-499). table_data is per-receipt, not
    # per-day, so this caches monthly summaries.
    "receipts": Report(
        id="receipts",
        title="Receipts",
        description="Receipt count, total sales value, discounts and refund/credit-note count",
        endpoint="/receipts",
        kind="scalar",
        grain="month",
        metrics=(
            Metric("receipt_count", "Receipt count", "sum"),
            Metric("total_sum", "Total receipt value", "sum"),
            Metric("tot_discount", "Total discount", "sum"),
            Metric("number_of_refunds_and_credit_note", "Refunds/CN count", "sum"),
            Metric("total_customers", "Unique customers", "non_additive"),
            # Derived, not returned by the API — ReceiptsController's summary has
            # exactly the five keys above. Declared as a ratio so aggregate()
            # recomputes it from summed numerator/denominator instead of
            # averaging per-month averages.
            Metric("avg_receipt_value", "Average receipt value", "ratio", num="total_sum", den="receipt_count"),
        ),
        answers=("receipts", "transactions", "number of sales", "average receipt", "basket value"),
    ),

    # RefundsController@calculateSummary — credit_note_type='CR' (cash refund).
    "refunds": Report(
        id="refunds",
        title="Refunds",
        description="Cash refund amount and count for the period",
        endpoint="/refunds",
        kind="scalar",
        grain="month",
        metrics=(
            Metric("total_amount_of_refunds", "Total refund amount", "sum"),
            Metric("total_no_of_refunds", "Refund count", "sum"),
            Metric("total_unique_customers", "Unique customers refunded", "non_additive"),
        ),
        answers=("refunds", "cash refund", "returns"),
    ),

    # CreditNotesController@calculateSummary — credit_note_type='CN'.
    "credit_notes": Report(
        id="credit_notes",
        title="Credit Notes",
        description="Credit note amount and count for the period",
        endpoint="/credit_notes",
        kind="scalar",
        grain="month",
        metrics=(
            Metric("total_amount_of_credit_notes", "Total credit note amount", "sum"),
            Metric("total_no_of_credit_notes", "Credit note count", "sum"),
            Metric("total_unique_customers", "Unique customers credited", "non_additive"),
        ),
        answers=("credit notes", "store credit"),
    ),

    # TaxesController — summary keys confirmed at formatSummary (lines ~200-202).
    "taxes": Report(
        id="taxes",
        title="Taxes",
        description="Taxable/non-taxable sales and tax amount collected",
        endpoint="/taxes",
        kind="scalar",
        grain="month",
        metrics=(
            Metric("total_taxable_sales", "Taxable sales", "sum"),
            Metric("total_non_taxable_sales", "Non-taxable sales", "sum"),
            Metric("total_tax_amount", "Tax amount", "sum"),
            Metric("total_net_sales", "Net sales", "sum"),
            Metric("total_taxable_sold_qty", "Taxable quantity sold", "sum"),
        ),
        answers=("taxes", "vat", "tax collected", "taxable sales"),
    ),

    # ChargesController@calculateSummary (lines ~285-296).
    "charges": Report(
        id="charges",
        title="Charges",
        description="Service/other charge amount and count for the period",
        endpoint="/charges",
        kind="scalar",
        grain="month",
        metrics=(
            Metric("total_charges", "Total charges", "sum"),
            Metric("no_of_charges", "Charge line count", "sum"),
        ),
        answers=("charges", "service charge"),
    ),

    # SalesByProductsController — one table_data row per product; unit-level
    # product_cost/product_price are non_additive.
    "sales_by_products": Report(
        id="sales_by_products",
        title="Sales by Product",
        description="Per-product sales, quantity, discount, refunds, cost and profit",
        endpoint="/sales_by_products",
        kind="dimensional",
        grain="month",
        dim_fields=("product_name",),
        metrics=(
            Metric("qty", "Quantity sold", "sum"),
            Metric("cost", "Total cost", "sum"),
            Metric("gross_sale", "Gross sale", "sum"),
            Metric("discount", "Discount", "sum"),
            Metric("refund", "Refund amount", "sum"),
            Metric("refund_qty", "Refund quantity", "sum"),
            Metric("net_sale", "Net sale", "sum"),
            Metric("product_gross_profit", "Gross profit", "sum"),
            Metric("product_cost", "Unit cost", "non_additive"),
            Metric("product_price", "Unit price", "non_additive"),
            Metric("profit_margin", "Profit margin %", "ratio", num="product_gross_profit", den="net_sale"),
        ),
        answers=("best selling", "top products", "product sales", "product profit", "slow moving"),
        extra_params=("category_id", "subcategory_id"),
    ),

    # SalesByCategoryController@formatData — row per category/subcategory.
    "sales_by_category": Report(
        id="sales_by_category",
        title="Sales by Category",
        description="Per-category sales, quantity, discount, refunds, cost and profit",
        endpoint="/sales_by_category",
        kind="dimensional",
        grain="month",
        dim_fields=("product_category", "sub_category"),
        metrics=(
            Metric("sold_qty", "Quantity sold", "sum"),
            Metric("refund_qty", "Refund quantity", "sum"),
            Metric("gross_sale", "Gross sale", "sum"),
            Metric("refunds", "Refund amount", "sum"),
            Metric("discounts", "Discount", "sum"),
            Metric("net_sales", "Net sale", "sum"),
            Metric("product_cost", "Total cost", "sum"),
            Metric("profit", "Gross profit", "sum"),
            Metric("profit_margin", "Profit margin %", "ratio", num="profit", den="net_sales"),
        ),
        answers=("category sales", "category profit", "best category"),
        extra_params=("category_id", "subcategory_id"),
    ),
}


def _simple(id: str, title: str, description: str, answers: tuple,
            cacheable: bool = True) -> Report:
    """Long-tail report: no per-metric declarations yet. Its whole data.summary
    block is cached per closed month (every value parsed as a number where
    possible) and treated as NON-additive — multi-month questions fetch the
    exact range live, single cached months read from cache. Row detail is
    always available via the live detail tool. Promote a report to a full
    declaration (like the core 8 above) when it earns real usage."""
    return Report(id=id, title=title, description=description,
                  endpoint=f"/{id}", kind="scalar", grain="month",
                  metrics=(), answers=answers, cacheable=cacheable)


# The remaining report APIs (routes/app.php index routes). Endpoint == /<id>.
for _r in (
    _simple("voucher_receipts", "Voucher Receipts",
            "Gift/voucher sales and redemption receipts",
            ("voucher", "gift card")),
    _simple("sales_by_discounts", "Sales by Discounts",
            "Sales grouped by discount type and value",
            ("discounts given", "discount breakdown", "promo")),
    _simple("sales_by_payment_types", "Sales by Payment Types",
            "Sales totals per payment method (cash, card, credit, ...)",
            ("payment type", "cash vs card", "payment method")),
    _simple("shifts", "Shifts",
            "Cashier shift openings, closings and totals",
            ("shift", "cash drawer", "opening", "closing")),
    _simple("digital_orders", "Digital Orders",
            "Online/digital channel orders",
            ("online orders", "digital orders", "web orders")),
    _simple("grn_tax_wise", "GRN Taxes",
            "Taxes on goods received notes (purchases)",
            ("grn", "purchase tax", "goods received")),
    _simple("purchase_tax_vs_sales_tax", "Purchase vs Sales Tax",
            "Comparison of tax paid on purchases vs tax collected on sales",
            ("purchase tax", "input output tax", "tax comparison")),
    _simple("alternative_currency_receipts", "Alternative Currency Receipts",
            "Receipts settled in a non-default currency",
            ("foreign currency", "alternative currency")),
    # Stock/inventory. Endpoint ids verified against routes/app.php — note the
    # HYPHENS: these two are '/stock-history' and '/inventory-expiry', unlike
    # every other report, which uses underscores.
    _simple("stock-history", "Stock History",
            "Stock movement history per product (receipts, transfers, adjustments)",
            ("stock history", "stock movement", "stock on date", "historical stock"),
            cacheable=False),
    _simple("inventory-expiry", "Inventory Expiry",
            "GRN-based stock nearing or past expiry",
            ("expiry", "expiring stock", "shelf life", "inventory value",
             "stock value"),
            cacheable=False),
    _simple("product_wise_receipts", "Product-wise Receipts",
            "Receipts that contain a given product",
            ("receipts with product", "who bought")),
    _simple("sales_by_trend", "Sales by Trend",
            "Sales trend buckets (hour/day/week patterns)",
            ("trend", "peak hours", "busiest")),
    _simple("deleted_receipts", "Deleted Receipts",
            "Deleted receipts audit trail",
            ("deleted receipts", "voided")),
    _simple("hold_receipts", "Hold Receipts",
            "Currently held/parked receipts",
            ("held receipts", "parked", "on hold"), cacheable=False),
    _simple("order_type_wise_sales", "Sales by Order Type",
            "Sales per order type (dine-in, takeaway, delivery, ...)",
            ("order type", "dine-in", "takeaway", "delivery")),
    _simple("sales_by_modifiers", "Sales by Modifiers",
            "Sales of modifiers/add-ons attached to products",
            ("modifiers", "add-ons", "extras")),
    _simple("modifier_sales", "Modifier Sales",
            "Detailed modifier sales report",
            ("modifier sales",)),
    _simple("sales_by_orders", "Sales by Orders",
            "Sales grouped at order level",
            ("orders", "order sales")),
    _simple("staff_sales", "Staff Sales",
            "Sales per staff member / employee",
            ("staff sales", "employee sales", "who sold")),
    _simple("ecommerce_sales", "E-commerce Sales",
            "Sales through the e-commerce channel",
            ("ecommerce", "online store")),
    _simple("service_area_usage", "Service Area Usage",
            "Table/service area usage and turnover",
            ("tables", "service area", "seating")),
    _simple("settlement_by_payment_type", "Settlement by Payment Type",
            "Settled amounts per payment method",
            ("settlement", "settled", "payment settlement")),
    _simple("employee_wise_product_sales", "Employee-wise Product Sales",
            "Products sold per employee",
            ("employee product sales", "who sold what", "staff product sales")),
    _simple("removed_products_in_hold_receipt", "Removed Products in Hold Receipt",
            "Products removed from a held receipt (suspicious activity)",
            ("removed from hold receipt",)),
    _simple("product_wise_tax_charge", "Product-wise Taxes & Charges",
            "Taxes and charges broken down per product",
            ("product tax", "product charges")),
    _simple("product_wise_taxes", "Product-wise Taxes",
            "Taxes broken down per product",
            ("tax per product",)),
    _simple("manual_receipts", "Manual Receipts",
            "Manually entered receipts",
            ("manual receipts",)),
    _simple("product_wise_orders", "Product-wise Orders",
            "Orders broken down per product",
            ("orders per product",)),
    _simple("product_sales_other_staff", "Product Sales by Other Staff",
            "Products sold by non-cashier staff",
            ("other staff sales",)),
    _simple("products_consumed_other_staff", "Products Consumed by Staff",
            "Products consumed internally by staff",
            ("staff consumption", "internal use")),
    _simple("product_wise_deleted_receipts", "Product-wise Deleted Receipts",
            "Deleted receipts per product (suspicious activity)",
            ("deleted per product",)),
    _simple("suspicious_refunds_credit_notes", "Suspicious Refunds & Credit Notes",
            "Potentially suspicious refund/credit-note activity",
            ("suspicious refunds", "fraud")),
    _simple("removed_products_in_hold", "Removed Products in Hold Receipts",
            "Products removed from held receipts (suspicious activity)",
            ("removed from hold",)),
    _simple("tip_transactions", "Tip Transactions",
            "Tip transactions (suspicious activity report)",
            ("tips detail", "tip transactions")),
):
    REPORTS[_r.id] = _r

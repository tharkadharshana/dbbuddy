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

"""
report_cache/registry.py
=========================
Declarative catalog of the 8 SalesPlay POS report APIs this cache backs.
Single source of truth for: endpoint path, params, grain, and per-metric
aggregability (doc 09 C3 — see docs/09_Report_Cache_Plan_Review.md Part 3).

Field names below are NOT guessed — they were read directly from the Laravel
controllers in docs/salesplay-internal-api-v2/app/Http/Controllers/App/Reports/
StandardReports/*.php (2026-07). Each report's comment cites the controller.

Metric.agg semantics (doc 09 C3):
  sum          — safe to SUM across days/months (e.g. gross_sales)
  ratio        — must be recomputed from summed num/den, never summed itself
                 (e.g. avg_ticket = SUM(total_sum) / SUM(receipt_count))
  non_additive — distinct counts / unit-level values / range-only totals.
                 Summing cached daily rows gives a WRONG answer — the exact
                 requested range must be re-fetched from the POS report API.

Which fields become a report's metrics: for scalar reports the metrics are
the API's `data.summary` block (the aggregate over whatever date range was
queried — daily ingestion calls with start_date == end_date to get one day's
summary). The exception is sales_summary, whose `data.table_data` rows are
themselves already grouped by day (`GROUP BY DATE` server-side, confirmed in
SalesSummaryController@getMainSalesData) — those per-day fields are the
metrics for that report instead. For dimensional reports (sales_by_products,
sales_by_category) the metrics are the per-row fields in `data.table_data`,
one row per product/category per month.

Endpoint paths: `report_cache/client.py` resolves its base URL from the
SAME env var embed.py's SalesPlay proxy already uses (`SALESPLAY_EMBED_PROXY_BASE`,
e.g. ".../rest/v2.0/public/app") — that value already includes the Laravel
`Route::prefix('app')` segment routes/app.php is mounted under (confirmed in
RouteServiceProvider.php). So `Report.endpoint` below is relative to that
base and must NOT repeat "/app/" (e.g. "/sales_summary", not "/app/sales_summary")
— doing so would build a broken ".../public/app/app/sales_summary" URL.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

Agg = Literal["sum", "ratio", "non_additive"]


@dataclass(frozen=True)
class Metric:
    key: str                        # key in the normalized payload, e.g. "net_sales"
    label: str                      # human label
    agg: Agg                        # sum | ratio | non_additive
    num: Optional[str] = None       # for ratio: numerator metric key
    den: Optional[str] = None       # for ratio: denominator metric key
    aliases: tuple = ()             # alternate raw API key(s) for the same value —
                                     # e.g. sales_summary's table_data rows use
                                     # "tips_amount"/"surcharge_amount" but its
                                     # summary block uses "tips"/"surcharge" for
                                     # the same figures (confirmed in
                                     # SalesSummaryController: formatTableDataNumbers
                                     # vs calculateSummaryTotals)


@dataclass(frozen=True)
class Report:
    id: str                         # 'sales_summary'
    title: str
    description: str                # what business question it answers (used for routing)
    endpoint: str                   # '/sales_summary' — relative to SALESPLAY_EMBED_PROXY_BASE, which already includes "/app"
    kind: Literal["scalar", "dimensional"]
    grain: Literal["day", "month"]
    dim_type: Optional[str]         # 'product' | 'category' for dimensional, else None
    metrics: tuple                  # tuple[Metric, ...]
    params: tuple                   # tuple[str, ...] — accepted API params
    answers: tuple                  # tuple[str, ...] — keyword hints for routing
    daily_cacheable: bool = False   # True only when this report's table_data is genuinely
                                     # GROUP-BY-DATE (per-day additive rows) so a closed range
                                     # can be answered by SUMMING cached daily facts (PLAN 05
                                     # aggregate_scalar). For scalar reports whose table_data is
                                     # per-RECEIPT (receipts/refunds/taxes/…), the daily fact is
                                     # NOT a valid per-day breakdown — those are always answered by
                                     # a live exact-range summary fetch (still dashboard-correct,
                                     # just not cached). Only sales_summary qualifies today
                                     # (see report_cache/normalize.py:normalize_daily_rows).


# Params common to every standard report (BaseReportRequest rules, all 8 controllers).
_COMMON_PARAMS = (
    "start_date", "end_date", "from_time", "to_time",
    "shop_id", "customer_id", "cashier_id", "page", "per_page", "search",
)

REPORTS: dict = {

    # SalesSummaryController@index — data.table_data rows are GROUP BY DATE
    # already (getMainSalesData SQL), formatTableDataNumbers() field names below.
    # Summary-only fields (calculateSummaryTotals) that are NOT decomposable per
    # day — operating_expenses is a whole-range payout total — are non_additive.
    "sales_summary": Report(
        id="sales_summary",
        title="Sales Summary",
        description="Daily gross/net sales, refunds, discounts, taxes, charges, gross profit",
        endpoint="/sales_summary",
        kind="scalar",
        grain="day",
        dim_type=None,
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
            # range-only summary fields (calculateSummaryTotals) — not present in
            # daily table_data rows, cannot be validly summed across days.
            Metric("operating_expenses", "Operating expenses", "non_additive"),
            Metric("operating_profit", "Operating profit", "non_additive"),
            Metric("net_profit", "Net profit", "non_additive"),
        ),
        params=_COMMON_PARAMS,
        answers=("sales", "revenue", "gross sales", "net sales", "profit", "discount", "tax summary"),
        daily_cacheable=True,  # table_data is GROUP BY DATE — the one report with valid daily facts
    ),

    # ReceiptsController@index — data.summary block (empty-payload shape + the
    # populated shape both confirmed in controller). table_data is per-receipt,
    # not per-day, so daily ingestion uses the summary via start_date==end_date.
    "receipts": Report(
        id="receipts",
        title="Receipts",
        description="Receipt count, total sales value, discounts and refund/CN count",
        endpoint="/receipts",
        kind="scalar",
        grain="day",
        dim_type=None,
        metrics=(
            Metric("receipt_count", "Receipt count", "sum"),
            Metric("total_sum", "Total receipt value", "sum"),
            Metric("tot_discount", "Total discount", "sum"),
            Metric("number_of_refunds_and_credit_note", "Refunds/CN count", "sum"),
            Metric("total_customers", "Unique customers", "non_additive"),
            Metric("avg_receipt_value", "Average receipt value", "ratio", num="total_sum", den="receipt_count"),
        ),
        params=_COMMON_PARAMS,
        answers=("receipts", "transactions", "number of sales", "average receipt", "basket value"),
    ),

    # RefundsController@calculateSummary — device_backup_credit_note_partitioned
    # rows with credit_note_type='CR' (cash refund).
    "refunds": Report(
        id="refunds",
        title="Refunds",
        description="Cash refund amount and count for the period",
        endpoint="/refunds",
        kind="scalar",
        grain="day",
        dim_type=None,
        metrics=(
            Metric("total_amount_of_refunds", "Total refund amount", "sum"),
            Metric("total_no_of_refunds", "Refund count", "sum"),
            Metric("total_unique_customers", "Unique customers refunded", "non_additive"),
        ),
        params=_COMMON_PARAMS,
        answers=("refunds", "cash refund", "returns"),
    ),

    # CreditNotesController@calculateSummary — same query as refunds but
    # credit_note_type='CN' (credit note, not cash refund).
    "credit_notes": Report(
        id="credit_notes",
        title="Credit Notes",
        description="Credit note amount and count for the period",
        endpoint="/credit_notes",
        kind="scalar",
        grain="day",
        dim_type=None,
        metrics=(
            Metric("total_amount_of_credit_notes", "Total credit note amount", "sum"),
            Metric("total_no_of_credit_notes", "Credit note count", "sum"),
            Metric("total_unique_customers", "Unique customers credited", "non_additive"),
        ),
        params=_COMMON_PARAMS,
        answers=("credit notes", "store credit"),
    ),

    # TaxesController@getSalesTaxesData totals block.
    "taxes": Report(
        id="taxes",
        title="Taxes",
        description="Taxable/non-taxable sales and tax amount collected",
        endpoint="/taxes",
        kind="scalar",
        grain="day",
        dim_type=None,
        metrics=(
            Metric("total_taxable_sales", "Taxable sales", "sum"),
            Metric("total_non_taxable_sales", "Non-taxable sales", "sum"),
            Metric("total_tax_amount", "Tax amount", "sum"),
            Metric("total_net_sales", "Net sales (taxable+non-taxable)", "sum"),
            Metric("total_taxable_sold_qty", "Taxable qty sold", "sum"),
        ),
        params=_COMMON_PARAMS,
        answers=("taxes", "vat", "tax collected", "taxable sales"),
    ),

    # ChargesController@calculateSummary.
    "charges": Report(
        id="charges",
        title="Charges",
        description="Service/other charge amount and count for the period",
        endpoint="/charges",
        kind="scalar",
        grain="day",
        dim_type=None,
        metrics=(
            Metric("total_charges", "Total charges", "sum"),
            Metric("no_of_charges", "Charge line count", "sum"),
        ),
        params=_COMMON_PARAMS,
        answers=("charges", "service charge"),
    ),

    # SalesByProductsController@getProductSalesData / formatSalesByProductsData
    # — one table_data row per product per queried range; ingested monthly.
    # product_cost/product_price are unit-level (per-item) values, not totals —
    # non_additive.
    "sales_by_products": Report(
        id="sales_by_products",
        title="Sales by Product",
        description="Per-product sales, quantity, discount, refunds, cost and profit",
        endpoint="/sales_by_products",
        kind="dimensional",
        grain="month",
        dim_type="product",
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
        params=_COMMON_PARAMS + ("category_id", "subcategory_id"),
        answers=("best selling", "top products", "product sales", "product profit", "slow moving"),
    ),

    # SalesByCategoryController@processDataWithAddons / formatData — one
    # table_data row per category per queried range; ingested monthly.
    "sales_by_category": Report(
        id="sales_by_category",
        title="Sales by Category",
        description="Per-category sales, quantity, discount, refunds, cost and profit",
        endpoint="/sales_by_category",
        kind="dimensional",
        grain="month",
        dim_type="category",
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
        params=_COMMON_PARAMS + ("category_id", "subcategory_id"),
        answers=("category sales", "category profit", "best category"),
    ),
}

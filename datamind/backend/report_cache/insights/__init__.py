"""
report_cache.insights — forecasting, anomaly/trend detection, and grounded
business-suggestion synthesis (PLAN 06). All feed on the report cache so inputs
are trustworthy and cheap (doc 09 C1 daily series, C3 additivity).

Public entry points:
  - forecast.forecast_metric(...)        — Prophet forecast over a cached daily series
  - trends.detect_anomalies / growth_summary
  - insight.generate_insight(...)        — orchestrated data+knowledge advice
  - tools.register_insight_tools(mcp, rctx) — expose the above to the report loop
Everything is gated by INSIGHTS_ENABLED (default OFF) at the tool/route layer.
"""

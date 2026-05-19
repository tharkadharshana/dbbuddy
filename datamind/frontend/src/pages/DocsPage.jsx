import React, { useState } from 'react'
import { Card } from '../components/UI'

// ── Shared small components ───────────────────────────────────────────────────

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 36 }}>
      <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 12, color: 'var(--text)' }}>{title}</div>
      {children}
    </div>
  )
}

function P({ children }) {
  return <p style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, marginBottom: 10, marginTop: 0 }}>{children}</p>
}

function CodeBlock({ children }) {
  return (
    <pre style={{
      background: 'var(--bg3)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '12px 16px', fontSize: 12,
      fontFamily: 'var(--mono)', color: 'var(--text)', overflowX: 'auto',
      marginBottom: 12, marginTop: 4,
    }}>{children}</pre>
  )
}

function Table({ headers, rows }) {
  return (
    <div style={{ overflowX: 'auto', marginBottom: 12 }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {headers.map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
              {row.map((cell, j) => (
                <td key={j} style={{ padding: '9px 12px', color: j === 0 ? 'var(--text)' : 'var(--text3)', fontFamily: j > 0 ? 'var(--mono)' : undefined }}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Callout({ color = 'blue', children }) {
  const map = {
    blue:  { bg: 'rgba(79,142,247,0.08)', border: 'rgba(79,142,247,0.25)', text: 'var(--blue)' },
    amber: { bg: 'rgba(245,166,35,0.08)', border: 'rgba(245,166,35,0.25)', text: 'var(--amber)' },
    green: { bg: 'rgba(34,197,94,0.08)',  border: 'rgba(34,197,94,0.25)',  text: 'var(--green)' },
  }
  const c = map[color] || map.blue
  return (
    <div style={{
      background: c.bg, border: `1px solid ${c.border}`,
      borderRadius: 8, padding: '10px 14px', fontSize: 13,
      color: 'var(--text2)', marginBottom: 12,
    }}>{children}</div>
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

const TABS = ['How Tokens Work', 'Operation Costs', 'Examples', 'Plans & Limits', 'FAQ']

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DocsPage() {
  const [tab, setTab] = useState(TABS[0])

  return (
    <div style={{ maxWidth: 860, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>How Billing Works</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
          Full transparency on how DataMind measures and charges your usage
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
            border: '1px solid',
            borderColor: tab === t ? 'var(--blue)' : 'var(--border)',
            background:  tab === t ? 'var(--blue-dim)' : 'transparent',
            color:       tab === t ? 'var(--blue)' : 'var(--text3)',
          }}>{t}</button>
        ))}
      </div>

      <Card style={{ padding: '24px 28px' }}>
        {tab === 'How Tokens Work' && <HowTokensWork />}
        {tab === 'Operation Costs' && <OperationCosts />}
        {tab === 'Examples'        && <Examples />}
        {tab === 'Plans & Limits'  && <PlansAndLimits />}
        {tab === 'FAQ'             && <FAQ />}
      </Card>
    </div>
  )
}

// ── Tab content ───────────────────────────────────────────────────────────────

function HowTokensWork() {
  return (
    <div>
      <Section title="What is a Token?">
        <P>
          A <strong>Token</strong> is the single unit DataMind uses to measure all usage — whether you are asking a
          natural language question, running a prebuilt analytics template, generating a forecast, or syncing data from
          an external integration. One number, one balance, one limit.
        </P>
        <P>
          Tokens reflect the actual computational resources your operations consume: the AI processing involved, the
          volume of data returned, and the compute work performed. Operations that touch more data or do more work cost
          more Tokens automatically.
        </P>
      </Section>

      <Section title="The Formula">
        <P>Every operation is scored with the same three-part formula:</P>
        <CodeBlock>{`Tokens = (AI tokens used  ÷ 1,000)
       + (rows returned    ÷ 1,000)
       + feature cost

Minimum charge per operation: 0.1 Tokens`}</CodeBlock>

        <Table
          headers={['Component', 'What it measures', 'Scale']}
          rows={[
            ['AI tokens used',  'Actual tokens processed by the language model (input + output)', '1,000 LLM tokens = 1 Token'],
            ['Rows returned',   'Number of data rows your query or sync brings back',              '1,000 rows = 1 Token'],
            ['Feature cost',    'Flat compute cost for the operation type (ML, SQL, etc.)',        'Fixed per operation — see table'],
          ]}
        />

        <Callout color="blue">
          <strong>Why data volume matters.</strong> A user with 500 rows in their database and a user with 500,000 rows
          run the same template and get very different results back. The user with more data naturally uses more
          infrastructure — Tokens capture that fairly.
        </Callout>
      </Section>

      <Section title="Where Tokens come from">
        <P>
          Your plan comes with a monthly Token allowance. Tokens are consumed by every operation you perform.
          When you purchase an add-on pack, those Tokens are added to your balance and roll forward — they are never
          wasted at renewal.
        </P>
        <P>
          You can see your live Token balance and a breakdown of recent usage on the <strong>Usage</strong> page.
        </P>
      </Section>
    </div>
  )
}

function OperationCosts() {
  const ops = [
    ['Natural language query (AI)',     'Ask Your Data',         '0',   'rows ÷ 1,000',    'AI tokens ÷ 1,000', 'Total varies with question complexity and result size'],
    ['Prebuilt analytics template',     'All Analytics',         '1.0', 'rows ÷ 1,000',    '—',                 'No AI used; 1 Token flat + data volume'],
    ['RFM customer analysis',           'All Analytics',         '1.5', 'rows ÷ 1,000',    '—',                 'Multi-pass aggregation'],
    ['Cohort analysis',                 'All Analytics',         '1.5', 'rows ÷ 1,000',    '—',                 'Multi-pass aggregation'],
    ['Basket / market basket analysis', 'All Analytics',         '2.0', 'rows ÷ 1,000',    '—',                 'Cross-join computation'],
    ['Growth metrics',                  'All Analytics',         '1.0', 'rows ÷ 1,000',    '—',                 ''],
    ['Employee performance',            'All Analytics',         '1.0', 'rows ÷ 1,000',    '—',                 ''],
    ['Product velocity',                'All Analytics',         '1.0', 'rows ÷ 1,000',    '—',                 ''],
    ['Payment analysis',                'All Analytics',         '0.5', 'rows ÷ 1,000',    '—',                 ''],
    ['Location comparison',             'All Analytics',         '0.5', 'rows ÷ 1,000',    '—',                 ''],
    ['Forecasting (Prophet)',           'Forecasting',           '2.0', 'rows ÷ 1,000',    '—',                 'ML model fit + predict; heavier compute'],
    ['Anomaly detection',               'Anomaly Alerts',        '2.0', 'rows ÷ 1,000',    '—',                 'IsolationForest on full dataset'],
    ['Report generation (AI)',          'Reports',               '0',   'rows ÷ 1,000',    'AI tokens ÷ 1,000', 'LLM narrative + section data'],
    ['Integration sync (data import)',  'Connections',           '0',   'rows ÷ 1,000',    '—',                 'Charged per row imported'],
  ]

  return (
    <div>
      <Section title="Cost breakdown by operation">
        <P>
          Every chargeable operation has three cost components. The table shows what applies for each.
          Operations that use AI also consume AI tokens, which are metered separately through the language
          model provider.
        </P>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 11.5, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)', background: 'var(--bg3)' }}>
                {['Operation', 'Where in app', 'Feature cost', 'Data cost', 'AI cost', 'Notes'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ops.map(([op, where, feat, data, ai, note], i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 10px', color: 'var(--text)', fontWeight: 500 }}>{op}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--text3)', fontSize: 11 }}>{where}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--blue)', fontFamily: 'var(--mono)' }}>{feat}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--text3)', fontFamily: 'var(--mono)' }}>{data}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--text3)', fontFamily: 'var(--mono)' }}>{ai}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--text3)', fontSize: 11 }}>{note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Callout color="green" style={{ marginTop: 12 }}>
          <strong>Minimum charge.</strong> Every operation costs at least 0.1 Tokens, even if the data
          and AI components add up to less. This prevents zero-cost looping.
        </Callout>
      </Section>

      <Section title="How AI token metering works">
        <P>
          When you use a feature that calls an AI language model (Ask Your Data, Reports), the model returns
          the exact number of tokens it processed — both input (your question + database schema) and output
          (the generated SQL or narrative). That count is divided by 1,000 and added to your Token charge for
          that operation.
        </P>
        <P>
          The AI token count is real: it comes directly from the model provider's response and is logged
          on your Usage page with full detail.
        </P>
      </Section>
    </div>
  )
}

function Examples() {
  const examples = [
    {
      title: 'NL query on a small database',
      scenario: '800 LLM tokens used, query returns 12 rows',
      calc: `AI cost:      800 ÷ 1,000 = 0.80 Tokens
Data cost:    12  ÷ 1,000 = 0.01 Tokens  (minimum applied → 0.10)
Feature cost: 0
─────────────────────────────────────
Total:        0.90 Tokens`,
      note: 'The rows-returned component is below the 0.1 minimum, so it is rounded up separately before summing.',
    },
    {
      title: 'NL query on a large database',
      scenario: '1,200 LLM tokens used, query returns 8,500 rows',
      calc: `AI cost:      1,200 ÷ 1,000 = 1.20 Tokens
Data cost:    8,500 ÷ 1,000 = 8.50 Tokens
Feature cost: 0
─────────────────────────────────────
Total:        9.70 Tokens`,
      note: 'Same question, much larger dataset — more Tokens because more data was processed and returned.',
    },
    {
      title: 'Prebuilt template — small data',
      scenario: 'Revenue Trend template, 24 rows returned (no AI)',
      calc: `AI cost:      0
Data cost:    24 ÷ 1,000 = 0.024 → 0.10 Tokens (minimum)
Feature cost: 1.0 Tokens
─────────────────────────────────────
Total:        1.10 Tokens`,
      note: 'No language model involved. Flat feature cost plus data volume.',
    },
    {
      title: 'Prebuilt template — large integration data',
      scenario: 'Top Products template on 150,000 synced rows (no AI)',
      calc: `AI cost:      0
Data cost:    150,000 ÷ 1,000 = 150.0 Tokens
Feature cost: 1.0 Tokens
─────────────────────────────────────
Total:        151.0 Tokens`,
      note: 'The data volume component is the dominant cost for heavy data users.',
    },
    {
      title: 'Forecasting',
      scenario: 'Auto-forecast on 730 data points (2 years daily)',
      calc: `AI cost:      0
Data cost:    730 ÷ 1,000 = 0.73 Tokens
Feature cost: 2.0 Tokens  (ML model fit)
─────────────────────────────────────
Total:        2.73 Tokens`,
      note: 'Forecasting has a higher feature cost because a ML model is trained on every run.',
    },
    {
      title: 'Anomaly detection',
      scenario: '365 daily revenue values scanned',
      calc: `AI cost:      0
Data cost:    365 ÷ 1,000 = 0.37 Tokens
Feature cost: 2.0 Tokens  (IsolationForest)
─────────────────────────────────────
Total:        2.37 Tokens`,
      note: '',
    },
  ]

  return (
    <div>
      <Section title="Worked examples">
        <P>
          These examples show exactly how each Token charge is calculated for real operations.
          You can verify any charge on the Usage page — each row shows the operation type, AI token count, and row count.
        </P>
        {examples.map((ex, i) => (
          <div key={i} style={{
            marginBottom: 20, padding: '14px 18px',
            background: 'var(--bg3)', borderRadius: 10,
            border: '1px solid var(--border)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{ex.title}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>{ex.scenario}</div>
            <CodeBlock>{ex.calc}</CodeBlock>
            {ex.note && <div style={{ fontSize: 11.5, color: 'var(--text3)', fontStyle: 'italic' }}>{ex.note}</div>}
          </div>
        ))}
      </Section>
    </div>
  )
}

function PlansAndLimits() {
  return (
    <div>
      <Section title="Monthly Token allowances">
        <Table
          headers={['Plan', 'Tokens / month', 'Monthly price']}
          rows={[
            ['Starter', '500',    '$5'],
            ['Growth',  '1,500',  '$10'],
            ['Pro',     '10,000', '$25'],
          ]}
        />
        <P>
          Token allowances reset at the start of each billing period. Unused plan Tokens do not roll over,
          but <strong>add-on Tokens always roll over</strong> — they remain in your balance until used.
        </P>
      </Section>

      <Section title="Add-on packs">
        <P>
          If you run out of plan Tokens before the period ends, you can purchase add-on packs from the Billing page.
          Each pack adds to your balance immediately and is consumed after your plan allowance is exhausted.
        </P>
        <Callout color="green">
          Add-on Tokens are consumed only after the plan allowance is fully used. They never expire and carry
          forward across billing periods.
        </Callout>
      </Section>

      <Section title="What happens when you reach the limit">
        <P>
          When your Token balance reaches zero, DataMind will return a clear error message rather than silently
          failing. You can either wait for the next billing period, or purchase an add-on pack to continue immediately.
        </P>
        <P>
          Integration syncs that hit the limit mid-sync will commit the rows that already fit and report exactly
          how many rows were skipped. Your data is never silently dropped.
        </P>
      </Section>

      <Section title="How Token limits are checked">
        <P>
          Every operation checks your remaining Token balance <em>before</em> running — not after.
          This means you are never charged for an operation that was blocked by an insufficient balance.
          The check is instantaneous and does not count against your balance.
        </P>
      </Section>
    </div>
  )
}

function FAQ() {
  const items = [
    {
      q: 'Why do I use more Tokens than my colleague even though we run the same templates?',
      a: 'Tokens scale with the amount of data your operations touch. If you have significantly more rows in your database or integration, the same template will return more data and therefore cost more Tokens. This is intentional — it reflects the actual infrastructure cost of processing your data.',
    },
    {
      q: 'Do cached analytics templates cost fewer Tokens?',
      a: 'Yes. When a template SQL is served from cache, no language model is called, so the AI token component is zero. You only pay the data volume component (rows returned) plus the flat feature cost.',
    },
    {
      q: 'I ran a forecast and got 0 rows back because my data was too short. Was I still charged?',
      a: 'Yes. The minimum charge of 0.1 Tokens applies per operation attempt. If a forecast or anomaly detection fails due to insufficient data, the check and ML setup still occurred, so the minimum is applied.',
    },
    {
      q: 'Can I see a breakdown of exactly what consumed my Tokens?',
      a: 'Yes. The Usage page shows a full history of every chargeable operation, including the operation type, AI tokens used, rows charged, and the total Token cost. You can audit every deduction.',
    },
    {
      q: 'Do integration syncs count against my Token balance?',
      a: 'Yes. Every row imported from an external integration (SalesPlay, Loyverse, etc.) consumes Tokens at the rate of 1,000 rows = 1 Token. Large initial syncs will consume a noticeable portion of your balance, which is why higher-tier plans have larger Token limits.',
    },
    {
      q: 'What happens to add-on Tokens when I upgrade my plan?',
      a: 'Add-on Tokens are attached to your account, not your plan. They remain in your balance when you change plans and are consumed only after your new plan allowance is exhausted.',
    },
    {
      q: 'Are reports more expensive because they use AI?',
      a: 'Reports use the AI language model to generate the narrative, so they consume AI tokens on top of the data component. The cost depends on how long the narrative is and how many data sections are included. You can see the exact token count on the Usage page after each report.',
    },
    {
      q: 'What is the minimum I can be charged for any single operation?',
      a: '0.1 Tokens. This applies even if the computed cost (AI + data + feature) is mathematically lower.',
    },
  ]

  return (
    <div>
      <Section title="Frequently asked questions">
        {items.map((item, i) => (
          <div key={i} style={{ marginBottom: 20 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, color: 'var(--text)' }}>{item.q}</div>
            <P>{item.a}</P>
          </div>
        ))}
      </Section>
    </div>
  )
}

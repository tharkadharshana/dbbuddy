import React, { useState } from 'react'
import { Card } from '../components/UI'

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 10, color: 'var(--text)' }}>{title}</div>
      {children}
    </div>
  )
}

function P({ children }) {
  return <p style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.75, marginBottom: 10, marginTop: 0 }}>{children}</p>
}

function Table({ headers, rows }) {
  return (
    <div style={{ overflowX: 'auto', marginBottom: 12 }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {headers.map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
              {row.map((cell, j) => (
                <td key={j} style={{ padding: '9px 12px', color: j === 0 ? 'var(--text)' : 'var(--text3)' }}>{cell}</td>
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
    blue:  { bg: 'rgba(79,142,247,0.08)',  border: 'rgba(79,142,247,0.25)' },
    amber: { bg: 'rgba(245,166,35,0.08)',  border: 'rgba(245,166,35,0.25)' },
    green: { bg: 'rgba(34,197,94,0.08)',   border: 'rgba(34,197,94,0.25)'  },
  }
  const c = map[color] || map.blue
  return (
    <div style={{ background: c.bg, border: `1px solid ${c.border}`, borderRadius: 8, padding: '10px 14px', fontSize: 13, color: 'var(--text2)', marginBottom: 12 }}>
      {children}
    </div>
  )
}

const TABS = ['What are Tokens', 'What uses Tokens', 'Tips to save Tokens', 'Plans & Add-ons', 'FAQ']

export default function DocsPage() {
  const [tab, setTab] = useState(TABS[0])

  return (
    <div style={{ maxWidth: 820, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>How Billing Works</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>Everything you need to know about Tokens and how to get the most from your plan</div>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid',
            borderColor: tab === t ? 'var(--blue)' : 'var(--border)',
            background:  tab === t ? 'var(--blue-dim)' : 'transparent',
            color:       tab === t ? 'var(--blue)' : 'var(--text3)',
          }}>{t}</button>
        ))}
      </div>

      <Card style={{ padding: '24px 28px' }}>
        {tab === 'What are Tokens'       && <WhatAreTokens />}
        {tab === 'What uses Tokens'      && <WhatUsesTokens />}
        {tab === 'Tips to save Tokens'   && <TipsToSave />}
        {tab === 'Plans & Add-ons'       && <PlansAndAddons />}
        {tab === 'FAQ'                   && <FAQ />}
      </Card>
    </div>
  )
}

// ── Tab content ───────────────────────────────────────────────────────────────

function WhatAreTokens() {
  return (
    <div>
      <Section title="Tokens — your usage currency">
        <P>
          Tokens are the single unit DataMind uses to measure all activity on your account. Every time you
          ask a question, run an analytics template, generate a forecast, or import data from an integration,
          a small number of Tokens is deducted from your monthly balance.
        </P>
        <P>
          Your plan comes with a fixed Token allowance each month. You can see exactly how many you have left
          at any time on the <strong>Billing → Usage</strong> tab.
        </P>
      </Section>

      <Section title="What affects how many Tokens an operation uses?">
        <P>Two things drive Token consumption:</P>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 16 }}>
          {[
            {
              icon: '🗄',
              title: 'Data volume',
              desc: 'The more records your query or sync processes, the more Tokens it uses. A question over a small table costs far fewer Tokens than the same question over a table with hundreds of thousands of records.',
            },
            {
              icon: '⚙️',
              title: 'Operation type',
              desc: 'Simple analytics cost fewer Tokens. Heavier operations like forecasting, anomaly detection, or AI-powered queries cost more because they require significantly more compute.',
            },
          ].map(({ icon, title, desc }) => (
            <div key={title} style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
              <div style={{ fontSize: 20, marginBottom: 8 }}>{icon}</div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{title}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>{desc}</div>
            </div>
          ))}
        </div>

        <Callout color="green">
          <strong>Your usage scales with your data.</strong> If you have a large database, operations will
          naturally cost more Tokens than the same operations on a small one — because more real work is
          being done on your behalf.
        </Callout>
      </Section>

      <Section title="Where to track your usage">
        <P>
          The <strong>Billing → Usage</strong> tab shows your current Token balance, a progress bar,
          and a full history of every operation that consumed Tokens — including the operation type,
          the date, and exactly how many Tokens it cost. Nothing is hidden.
        </P>
      </Section>
    </div>
  )
}

function WhatUsesTokens() {
  const ops = [
    { op: 'Ask Your Data (AI query)',  cost: 'Medium–High', note: 'Depends on question complexity and result size' },
    { op: 'Analytics templates',       cost: 'Low–Medium',  note: 'Depends on how many records your data has' },
    { op: 'Report generation',         cost: 'Medium–High', note: 'Depends on number of sections and data size' },
    { op: 'Forecasting',               cost: 'Medium',      note: 'Higher compute — Growth & Pro plans only' },
    { op: 'Anomaly detection',         cost: 'Medium',      note: 'Higher compute — Growth & Pro plans only' },
    { op: 'RFM / Cohort / Basket',     cost: 'Low–Medium',  note: 'Multi-pass analysis over your data' },
    { op: 'Integration data sync',     cost: 'Low–Medium',  note: 'Scales with records imported — Pro plan only' },
  ]

  return (
    <div>
      <Section title="Operations and their Token cost">
        <P>
          Every operation in DataMind uses Tokens. The table below gives you a relative sense of cost
          so you can plan your usage effectively.
        </P>
        <Table
          headers={['Operation', 'Relative cost', 'Notes']}
          rows={ops.map(r => [r.op, r.cost, r.note])}
        />
        <Callout color="amber">
          <strong>Cost is not fixed.</strong> "Medium" for a 500-record table might be a fraction of a Token.
          The same operation on a 200,000-record table will cost significantly more because DataMind is processing
          far more of your data.
        </Callout>
      </Section>

      <Section title="Operations that do NOT use Tokens">
        <P>The following actions are free and never deduct from your balance:</P>
        <ul style={{ paddingLeft: 20, margin: '0 0 12px', color: 'var(--text2)', fontSize: 13, lineHeight: 2 }}>
          <li>Viewing your dashboard or any previously loaded result</li>
          <li>Navigating the app, changing settings, viewing your profile</li>
          <li>Checking your usage or billing information</li>
          <li>Connecting or disconnecting a data source (the subsequent sync uses Tokens)</li>
        </ul>
      </Section>
    </div>
  )
}

function TipsToSave() {
  const tips = [
    {
      title: 'Ask focused questions',
      desc: 'When using Ask Your Data, specific questions return fewer records than broad ones. "Show me top 10 products by revenue this month" will use far fewer Tokens than "show me all sales data".',
    },
    {
      title: 'Use analytics templates instead of free-form queries',
      desc: 'Prebuilt templates are optimised to return only the aggregated data you need. They typically cost fewer Tokens than writing a custom AI question that returns raw records.',
    },
    {
      title: 'Run forecasting and anomaly detection when you need it',
      desc: 'These are the heaviest operations. Run them on a schedule that makes sense for your business — daily if you need it, weekly if you don\'t.',
    },
    {
      title: 'Sync integrations during off-peak times',
      desc: 'Large initial syncs can use a significant portion of your Token balance. If you\'re connecting a new integration with years of history, consider upgrading your plan beforehand.',
    },
    {
      title: 'Upgrade before large operations',
      desc: 'If you\'re planning to sync a large new dataset or run extensive analysis, upgrading your plan before you start means you won\'t hit your limit mid-operation.',
    },
    {
      title: 'Use add-ons for occasional overages',
      desc: 'If you occasionally go over your plan limit, add-on Token packs are available. They roll over and never expire, so any unused Tokens carry to next month.',
    },
  ]

  return (
    <div>
      <Section title="Getting the most from your Tokens">
        {tips.map((tip, i) => (
          <div key={i} style={{ display: 'flex', gap: 14, marginBottom: 18 }}>
            <div style={{
              width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
              background: 'var(--blue-dim)', border: '1px solid rgba(79,142,247,0.3)',
              color: 'var(--blue)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 12, fontWeight: 700,
            }}>{i + 1}</div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{tip.title}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.65 }}>{tip.desc}</div>
            </div>
          </div>
        ))}
      </Section>
    </div>
  )
}

function PlansAndAddons() {
  return (
    <div>
      <Section title="Monthly Token allowances">
        <Table
          headers={['Plan', 'Tokens / month', 'Price', 'Data history']}
          rows={[
            ['Starter', '500',    '$5/mo',  '1 month'],
            ['Growth',  '1,500',  '$10/mo', '3 months'],
            ['Pro',     '10,000', '$25/mo', '1 year'],
          ]}
        />
        <P>
          Plan Tokens reset at the start of each billing period. Any unused plan Tokens do not carry forward,
          so make the most of them each month.
        </P>
      </Section>

      <Section title="Data history">
        <P>
          Each plan includes access to a different window of your historical data. Starter gives you
          the last month, Growth the last 3 months, and Pro the last full year. This applies to
          queries, analytics, forecasting, and synced integration data.
        </P>
        <Callout color="amber">
          Upgrading your plan immediately unlocks the longer history window for all future operations.
        </Callout>
      </Section>

      <Section title="Add-on Token packs">
        <P>
          If you run low before the end of your billing period, you can purchase add-on Token packs
          from the <strong>Billing → Plans & Add-ons</strong> tab. Each pack adds 50 Tokens to your balance.
        </P>
        <Callout color="green">
          <strong>Add-on Tokens never expire.</strong> Unlike your monthly plan allowance, add-on Tokens
          roll over every month and stay in your account until you use them.
        </Callout>
      </Section>

      <Section title="What happens when you run out">
        <P>
          When your Token balance reaches zero, DataMind will pause operations that would exceed your limit
          and show you a clear message. No silent failures — you always know exactly what happened and why.
          Purchase an add-on pack or wait for your next billing period to continue.
        </P>
      </Section>
    </div>
  )
}

function FAQ() {
  const items = [
    {
      q: 'Why did I use more Tokens than I expected?',
      a: 'The most common reason is data volume. Operations scale with the number of records in your database. If your data has grown significantly, the same queries will cost more Tokens than before. Check the Usage tab to see a breakdown of exactly which operations consumed your Tokens.',
    },
    {
      q: 'Do cached analytics templates cost fewer Tokens?',
      a: 'Yes. When DataMind serves an analytics result from its cache rather than re-running the full analysis, the Token cost is lower. The first run builds the cache; subsequent runs are cheaper.',
    },
    {
      q: 'Can I see a breakdown of my Token usage?',
      a: 'Yes — the Billing → Usage tab shows a full history of every chargeable operation with the date, operation type, and Token cost. You can audit every deduction.',
    },
    {
      q: 'What happens to my add-on Tokens if I upgrade my plan?',
      a: 'They stay in your account. Add-on Tokens are separate from your plan allowance and are consumed only after your monthly plan Tokens are exhausted.',
    },
    {
      q: 'Why do Forecasting and Anomaly Detection cost more?',
      a: 'These operations require running machine learning models against your data, which is significantly more compute-intensive than running a SQL query. They are available on Growth and Pro plans.',
    },
    {
      q: 'My Token balance dropped a lot after connecting an integration. Why?',
      a: 'When you connect an external integration for the first time, DataMind performs an initial sync to import your historical data. The Token cost scales with how many records are imported. Subsequent delta syncs only import new data and cost much less.',
    },
    {
      q: 'What is the minimum Token cost for any operation?',
      a: 'Every operation costs at least a small minimum amount to prevent abuse, even if very little data is involved. This is reflected in the Usage history.',
    },
  ]

  return (
    <div>
      <Section title="Frequently asked questions">
        {items.map((item, i) => (
          <div key={i} style={{ marginBottom: 22, paddingBottom: 22, borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none' }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, color: 'var(--text)' }}>{item.q}</div>
            <P>{item.a}</P>
          </div>
        ))}
      </Section>
    </div>
  )
}

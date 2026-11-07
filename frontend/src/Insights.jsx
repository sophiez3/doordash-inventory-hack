import { useState } from 'react'
import { API_URL, dollars, prettyName, usePolling } from './api.js'

export default function Insights() {
  // All the numbers come from our own server code and refresh every 5 seconds.
  const insights = usePolling('/insights', 5000)
  const data = insights.data

  // The AI briefing is on demand (a button), because AI calls cost money and take a moment.
  const [briefing, setBriefing] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const generate = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_URL}/insights/summary`, { method: 'POST' })
      if (!response.ok) throw new Error('Request failed')
      setBriefing({ ...(await response.json()), at: new Date() })
    } catch {
      setError("Couldn't generate the briefing. Is the server running?")
    } finally {
      setLoading(false)
    }
  }

  if (!data) {
    return (
      <div>
        <header><h1>Owner Insights</h1></header>
        {insights.error
          ? <div className="banner error">Can't reach the kitchen server. Is `fastapi dev main.py` running?</div>
          : <p className="empty">Loading…</p>}
      </div>
    )
  }

  const t = data.totals
  const maxMargin = Math.max(1, ...data.dishes.map((d) => d.margin_pct || 0))

  return (
    <div>
      <header>
        <h1>Owner Insights</h1>
        <p>Sales, profit, and what to reorder. Costs are estimated from your supplier prices.</p>
      </header>

      <div className="kpis">
        <div className="kpi"><span>Revenue</span><strong>{dollars(t.revenue_cents)}</strong></div>
        <div className="kpi"><span>Orders</span><strong>{t.orders}</strong></div>
        <div className="kpi"><span>Avg order</span><strong>{dollars(t.avg_order_cents)}</strong></div>
        <div className="kpi">
          <span>Gross profit</span>
          <strong>{dollars(t.gross_profit_cents)}</strong>
          <em>{t.gross_margin_pct == null ? 'no sales yet' : `${t.gross_margin_pct}% margin`}</em>
        </div>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Owner briefing</h2>
          <button onClick={generate} disabled={loading}>
            {loading ? 'Writing…' : briefing ? 'Refresh briefing' : 'Generate briefing'}
          </button>
        </div>
        {error && <div className="banner error small">{error}</div>}
        {!briefing && !error && (
          <p className="hint">An AI assistant turns the numbers on this page into a short plain-English summary.</p>
        )}
        {briefing && (
          <div className="briefing">
            <p>{briefing.summary}</p>
            {briefing.actions.length > 0 && (
              <ul>{briefing.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            )}
            <div className="source">
              {briefing.source === 'ai' ? 'Written by AI from the figures on this page' : 'Basic summary (AI unavailable)'}
              {' · '}{briefing.at.toLocaleTimeString()}
            </div>
          </div>
        )}
      </section>

      <div className="dash-grid">
        <section className="panel">
          <h2>Dish profitability</h2>
          <table>
            <thead>
              <tr><th>Dish</th><th>Price</th><th>Cost</th><th>Margin</th><th>Sold</th></tr>
            </thead>
            <tbody>
              {data.dishes.map((d) => (
                <tr key={d.id}>
                  <td className="name">
                    {d.name}
                    {!d.is_available && <span className="chip out tiny">86’d</span>}
                  </td>
                  <td>{dollars(d.price_cents)}</td>
                  <td>{dollars(d.cost_cents)}</td>
                  <td>
                    <div className="bar small-bar">
                      <div className="fill ok" style={{ width: `${((d.margin_pct || 0) / maxMargin) * 100}%` }} />
                    </div>
                    <span className="amount">{d.margin_pct}%</span>
                  </td>
                  <td>{d.units_sold}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <div className="side">
          <section className="panel">
            <h2>Reorder suggestions</h2>
            {data.reorder.length === 0 && <p className="empty">Nothing is running low.</p>}
            {data.reorder.map((r) => (
              <div key={r.ingredient} className="reorder">
                <div>
                  <strong>{prettyName(r.ingredient)}</strong>
                  <span className={`chip ${r.status} tiny`}>{r.status === 'out' ? 'Out' : 'Low'}</span>
                  <div className="amount">{r.on_hand} left ({r.percent_left}%)</div>
                </div>
                <div className="reorder-qty">
                  Order {r.suggested_qty} {r.unit}s
                  <div className="amount">about {dollars(r.estimated_cost_cents)}</div>
                </div>
              </div>
            ))}
            {data.reorder.length > 0 && (
              <p className="hint">Rule of thumb: reorder what has been used so far.</p>
            )}
          </section>

          <section className="panel">
            <h2>Sold out right now</h2>
            {data.sold_out.length === 0 && <p className="empty">Everything is available.</p>}
            {data.sold_out.map((s) => (
              <div key={s.name} className="reorder">
                <strong>{s.name}</strong>
                <span className="amount">needs {s.blocked_by.map(prettyName).join(', ')}</span>
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  )
}

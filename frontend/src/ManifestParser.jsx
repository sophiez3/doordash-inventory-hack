import { useState } from 'react'
import { API_URL, dollars, prettyName } from './api.js'

// A realistic messy delivery note, so you can demo without typing anything.
const SAMPLE_NOTE = `FRESH FOODS WHOLESALE  -  Delivery note #88213
Frzn beef patties 4oz  -  2 cs x 12 patties  @ $17.40/cs
Burger buns 8-pack  x3 packs   $3.20 per pack
Sliced cheese (1 slice ea), box of 48 .... $12.00
Cola 12oz cans - 24 @ 0.50 ea
Paper napkins - 1 case - $22.00`

export default function ManifestParser({ ingredients, onSaved }) {
  const [text, setText] = useState('')
  const [rows, setRows] = useState(null)       // the AI's draft, editable by the human
  const [unmatched, setUnmatched] = useState([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState({ text: '', ok: true })

  const readFile = (event) => {
    const file = event.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setText(String(reader.result).slice(0, 4000))
    reader.readAsText(file)
  }

  // Step 1: ask the AI for a draft. Nothing is saved yet.
  const parse = async () => {
    setLoading(true)
    setMessage({ text: '', ok: true })
    setRows(null)
    try {
      const response = await fetch(`${API_URL}/manifests/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      const data = await response.json()
      if (!response.ok) {
        setMessage({ text: typeof data.detail === 'string' ? data.detail : 'Could not parse.', ok: false })
      } else {
        setRows(data.rows.map((r) => ({ ...r, include: true, price: (r.unit_price_cents / 100).toFixed(2) })))
        setUnmatched(data.unmatched)
      }
    } catch {
      setMessage({ text: "Can't reach the kitchen server.", ok: false })
    } finally {
      setLoading(false)
    }
  }

  const edit = (index, field, value) =>
    setRows((current) => current.map((r, i) => (i === index ? { ...r, [field]: value } : r)))

  // Step 2: a human confirms, then (and only then) the rows are saved.
  const confirm = async () => {
    const chosen = rows.filter((r) => r.include)
    const items = chosen.map((r) => ({
      ingredient: r.ingredient,
      quantity: parseInt(r.quantity, 10),
      unit_price_cents: Math.round(parseFloat(r.price) * 100),
    }))
    if (items.length === 0 || items.some((i) => !(i.quantity > 0) || !(i.unit_price_cents >= 0))) {
      setMessage({ text: 'Check the quantities and prices (quantity above 0).', ok: false })
      return
    }
    try {
      const response = await fetch(`${API_URL}/shipments/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      })
      if (!response.ok) throw new Error('Request failed')
      setMessage({ text: `Added ${items.length} line${items.length === 1 ? '' : 's'} to inventory.`, ok: true })
      setRows(null)
      setUnmatched([])
      setText('')
      onSaved()
    } catch {
      setMessage({ text: 'Could not save the shipment. Nothing was changed.', ok: false })
    }
  }

  return (
    <section className="panel">
      <h2>AI supplier note parser</h2>
      <p className="hint">Paste a messy delivery note (or upload a .txt file). The AI drafts the rows; you review them before anything is saved.</p>

      <textarea
        rows={6}
        maxLength={4000}
        placeholder="Paste a supplier delivery note here..."
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row-buttons">
        <button onClick={() => setText(SAMPLE_NOTE)}>Load sample</button>
        <label className="file-button">
          Upload .txt
          <input type="file" accept=".txt,.csv,text/plain" onChange={readFile} hidden />
        </label>
      </div>
      <button className="primary" onClick={parse} disabled={loading || !text.trim()}>
        {loading ? 'Reading note…' : 'Parse with AI'}
      </button>

      {message.text && <div className={`banner ${message.ok ? 'success' : 'error'} small`}>{message.text}</div>}

      {rows && (
        <div className="draft">
          <h3>Review the draft</h3>
          {rows.length === 0 && <p className="empty">No inventory lines found.</p>}
          {rows.map((r, i) => (
            <div key={i} className={`draft-row ${r.include ? '' : 'off'}`}>
              <label className="check">
                <input type="checkbox" checked={r.include} onChange={(e) => edit(i, 'include', e.target.checked)} />
                <span className="source-line">{r.source_text}</span>
              </label>
              <div className="draft-fields">
                <select value={r.ingredient} onChange={(e) => edit(i, 'ingredient', e.target.value)}>
                  {ingredients.map((g) => (
                    <option key={g.ingredient} value={g.ingredient}>{prettyName(g.ingredient)}</option>
                  ))}
                </select>
                <input type="number" min="1" value={r.quantity} onChange={(e) => edit(i, 'quantity', e.target.value)} />
                <span>×</span>
                <input type="number" min="0" step="0.01" value={r.price} onChange={(e) => edit(i, 'price', e.target.value)} />
              </div>
              <div className="line-total">
                {r.quantity || 0} {r.unit}s at ${r.price || '0.00'} each ={' '}
                {dollars(Math.round((parseFloat(r.price) || 0) * 100) * (parseInt(r.quantity, 10) || 0))}
              </div>
            </div>
          ))}

          {unmatched.length > 0 && (
            <div className="banner error small">
              <strong>Not added (needs a human):</strong>
              {unmatched.map((u, i) => (
                <div key={i}>• {u.source_text} <em>({u.reason})</em></div>
              ))}
            </div>
          )}

          {rows.length > 0 && (
            <button className="primary" onClick={confirm}>Confirm and add to inventory</button>
          )}
        </div>
      )}
    </section>
  )
}

import { useState } from 'react'
import { API_URL, dollars, prettyName, localTime, modText, usePolling } from './api.js'
import ManifestParser from './ManifestParser.jsx'

export default function Kitchen() {
  // Three live feeds, each refreshed every 3 seconds.
  const inventory = usePolling('/inventory')
  const orders = usePolling('/orders')
  const menu = usePolling('/menu')
  const ai = usePolling('/ai/status', 30000)

  // Restock form state. Empty means "use the default".
  const [pickedIngredient, setPickedIngredient] = useState('')
  const [quantity, setQuantity] = useState('10')
  const [priceInput, setPriceInput] = useState('')
  const [message, setMessage] = useState({ text: '', ok: true })

  const stock = inventory.data || []
  const soldOut = (menu.data || []).filter((m) => !m.is_available)

  // Derived values (computed each render, no extra state needed).
  const ingredient = pickedIngredient || stock[0]?.ingredient || ''
  const selectedRow = stock.find((r) => r.ingredient === ingredient)
  const defaultPrice = selectedRow?.last_unit_price_cents != null
    ? (selectedRow.last_unit_price_cents / 100).toFixed(2)
    : '0.00'
  const price = priceInput !== '' ? priceInput : defaultPrice

  const restock = async () => {
    const qty = parseInt(quantity, 10)
    const unitPrice = parseFloat(price)
    if (!ingredient || !(qty > 0) || !(unitPrice >= 0)) {
      setMessage({ text: 'Enter a quantity above 0 and a valid price.', ok: false })
      return
    }
    try {
      const response = await fetch(`${API_URL}/shipments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ingredient,
          quantity: qty,
          unit_price_cents: Math.round(unitPrice * 100),
        }),
      })
      if (!response.ok) throw new Error('Request failed')
      setMessage({ text: `Received ${qty} × ${prettyName(ingredient)}.`, ok: true })
      setPriceInput('')
      inventory.reload()
      menu.reload()
    } catch {
      setMessage({ text: 'Could not record the shipment.', ok: false })
    }
  }

  // How "full" an ingredient is, compared with everything we've ever received.
  const fraction = (row) => (row.total_received > 0 ? row.quantity_on_hand / row.total_received : 0)
  const status = (row) => {
    if (row.quantity_on_hand - row.reserve_threshold <= 0) return 'out'
    if (fraction(row) <= 0.3) return 'low'
    return 'ok'
  }
  const statusLabel = { ok: 'OK', low: 'Low', out: 'Out' }

  const deliveryLabel = {
    confirmed: 'Ready for pickup',
    picked_up: 'Out for delivery',
    delivered: 'Delivered',
  }

  const offline = inventory.error || orders.error || menu.error

  return (
    <div>
      <header>
        <h1>Kitchen Dashboard</h1>
        <p>Live ingredient levels. They drop the moment an order is placed.</p>
        {ai.data && (
          <span className={`chip ${ai.data.configured ? 'ok' : 'low'}`}>
            {ai.data.configured ? `AI connected: ${ai.data.model}` : 'AI not configured: using basic matching'}
          </span>
        )}
      </header>

      {offline && (
        <div className="banner error">
          Can't reach the kitchen server. Is `fastapi dev main.py` running?
        </div>
      )}

      <div className="dash-grid">
        <section className="panel">
          <h2>Ingredient inventory</h2>
          <table>
            <thead>
              <tr>
                <th>Ingredient</th>
                <th>Stock</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {stock.map((row) => (
                <tr key={row.ingredient}>
                  <td className="name">{prettyName(row.ingredient)}</td>
                  <td>
                    <div className="bar">
                      <div className={`fill ${status(row)}`} style={{ width: `${fraction(row) * 100}%` }} />
                    </div>
                    <span className="amount">
                      {row.quantity_on_hand} of {row.total_received} {row.unit}
                    </span>
                  </td>
                  <td>
                    <span className={`chip ${status(row)}`}>{statusLabel[status(row)]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <div className="side">
          <section className="panel">
            <h2>Automatically 86'd dishes</h2>
            {soldOut.length === 0 ? (
              <p className="empty">Everything is available.</p>
            ) : (
              <div className="chips">
                {soldOut.map((m) => (
                  <span key={m.id} className="chip out">{m.name}</span>
                ))}
              </div>
            )}
          </section>

          <ManifestParser
            ingredients={stock}
            onSaved={() => { inventory.reload(); menu.reload() }}
          />

          <section className="panel">
            <h2>Receive a shipment</h2>
            <label>
              Ingredient
              <select
                value={ingredient}
                onChange={(e) => { setPickedIngredient(e.target.value); setPriceInput('') }}
              >
                {stock.map((r) => (
                  <option key={r.ingredient} value={r.ingredient}>{prettyName(r.ingredient)}</option>
                ))}
              </select>
            </label>
            <label>
              Quantity {selectedRow ? `(${selectedRow.unit}s)` : ''}
              <input type="number" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </label>
            <label>
              Cost per unit ($)
              <input type="number" min="0" step="0.01" value={price} onChange={(e) => setPriceInput(e.target.value)} />
            </label>
            <button className="primary" onClick={restock}>Add to inventory</button>
            {message.text && (
              <div className={`banner ${message.ok ? 'success' : 'error'} small`}>{message.text}</div>
            )}
          </section>

          <section className="panel">
            <h2>Live order feed</h2>
            {(orders.data || []).length === 0 && <p className="empty">No orders yet.</p>}
            {(orders.data || []).map((o) => (
              <div key={o.id} className="feed-item">
                <div className="feed-top">
                  <strong>Order #{o.id}</strong>
                  <span>{localTime(o.created_at)}</span>
                </div>
                {o.items.map((i, n) => (
                  <div key={n} className="feed-line">
                    {i.quantity} × {i.name}
                    {i.notes && <div className="feed-note">“{i.notes}”</div>}
                    {i.modifications.length > 0 && (
                      <div className="feed-mods">{i.modifications.map(modText).join(', ')}</div>
                    )}
                  </div>
                ))}
                <div className="feed-total">
                  {dollars(o.total_cents)} · {o.driver_name || 'No driver'} · {deliveryLabel[o.status]}
                </div>
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  )
}

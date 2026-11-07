import { useState, useEffect, useCallback } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { API_URL, dollars, modText } from './api.js'

export default function Storefront() {
  // State = values React remembers. When one changes, the screen re-draws.
  const [menu, setMenu] = useState([])          // list of dishes from the server
  const [cart, setCart] = useState({})          // { menuItemId: quantity }
  const [notes, setNotes] = useState({})        // { menuItemId: "double cheese, no onions" }
  const [error, setError] = useState('')        // message to show the customer
  const [receipt, setReceipt] = useState(null)  // set after a successful order
  const [placing, setPlacing] = useState(false) // true while an order is sending

  // Ask the server for the current menu.
  const loadMenu = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/menu`)
      if (!response.ok) throw new Error('Bad response')
      const items = await response.json()
      setMenu(items)

      // If a dish in the cart just sold out, quietly remove it.
      setCart((current) => {
        const soldOut = items.filter((i) => !i.is_available).map((i) => i.id)
        const cleaned = { ...current }
        soldOut.forEach((id) => delete cleaned[id])
        return Object.keys(cleaned).length === Object.keys(current).length ? current : cleaned
      })
      setError((e) => (e.startsWith("Can't reach") ? '' : e))
    } catch {
      setError("Can't reach the kitchen server. Is `fastapi dev main.py` running?")
    }
  }, [])

  // Load the menu when the page opens, then again every 3 seconds (polling).
  // This is what makes dishes turn gray without refreshing the page.
  useEffect(() => {
    loadMenu()
    const timer = setInterval(loadMenu, 3000)
    return () => clearInterval(timer) // stop polling if the page closes
  }, [loadMenu])

  const changeQuantity = (id, change) => {
    setReceipt(null)
    setCart((current) => {
      const next = Math.max(0, (current[id] || 0) + change)
      const updated = { ...current, [id]: next }
      if (next === 0) delete updated[id]
      return updated
    })
  }

  // Build the cart lines (dish + quantity) and the total price.
  const cartLines = menu
    .filter((item) => cart[item.id])
    .map((item) => ({ ...item, quantity: cart[item.id] }))
  const totalCents = cartLines.reduce((sum, l) => sum + l.price_cents * l.quantity, 0)

  const placeOrder = async () => {
    setPlacing(true)
    setError('')
    try {
      const response = await fetch(`${API_URL}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: cartLines.map((l) => ({
            menu_item_id: l.id,
            quantity: l.quantity,
            notes: (notes[l.id] || '').trim() || null,
          })),
        }),
      })
      const data = await response.json()
      if (!response.ok) {
        // 409 = sold out / not enough stock. Show the server's message.
        setError(typeof data.detail === 'string' ? data.detail : 'Order could not be placed.')
      } else {
        setReceipt(data)
        setCart({})
        setNotes({})
      }
    } catch {
      setError("Can't reach the kitchen server. Is `fastapi dev main.py` running?")
    } finally {
      setPlacing(false)
      loadMenu() // refresh right away so sold-out dishes update
    }
  }

  const hasNotes = cartLines.some((l) => (notes[l.id] || '').trim())

  return (
    <div>
      <header>
        <h1>Smart Kitchen</h1>
        <p>Order food. Dishes turn gray the moment we run out.</p>
      </header>

      {error && <div className="banner error">{error}</div>}

      <div className="layout">
        <section className="menu">
          {menu.map((item) => (
            <div key={item.id} className={`card ${item.is_available ? '' : 'sold-out'}`}>
              <div>
                <h3>{item.name}</h3>
                <p className="price">{dollars(item.price_cents)}</p>
              </div>
              {item.is_available ? (
                <button onClick={() => changeQuantity(item.id, 1)}>Add</button>
              ) : (
                <span className="badge">Sold Out</span>
              )}
            </div>
          ))}
        </section>

        <aside className="cart">
          <h2>Your order</h2>

          {receipt && (
            <div className="banner success">
              <strong>Order #{receipt.order_id} confirmed!</strong>
              <div>Total: {dollars(receipt.total_cents)}</div>
              {receipt.driver_name && <div>Your driver: {receipt.driver_name}</div>}

              {(receipt.special_instructions || []).map((s) => (
                <div key={s.menu_item_id} className="instructions">
                  <strong>{s.name}</strong>: “{s.notes}”
                  {s.applied.length > 0 ? (
                    <div>
                      We’ll make it: {s.applied.map(modText).join(', ')}{' '}
                      <span className="source">
                        ({s.source === 'ai' ? 'understood by AI' : 'basic matching'})
                      </span>
                    </div>
                  ) : (
                    <div>Sent to the kitchen as written.</div>
                  )}
                  {s.applied.length > 0 && s.unhandled && (
                    <div>The kitchen will also read: “{s.unhandled}”</div>
                  )}
                </div>
              ))}

              <div className="qr-box">
                <QRCodeSVG value={receipt.qr_token} size={150} marginSize={2} />
                <div className="token">Bag label (the driver scans this)</div>
              </div>
            </div>
          )}

          {cartLines.length === 0 && !receipt && <p className="empty">Your cart is empty.</p>}

          {cartLines.map((line) => (
            <div key={line.id} className="cart-item">
              <div className="cart-line">
                <span>{line.name}</span>
                <span className="qty">
                  <button onClick={() => changeQuantity(line.id, -1)}>−</button>
                  {line.quantity}
                  <button onClick={() => changeQuantity(line.id, 1)}>+</button>
                </span>
              </div>
              <input
                type="text"
                className="note-input"
                maxLength={200}
                placeholder="Special instructions (e.g. double cheese, hold the onions)"
                value={notes[line.id] || ''}
                onChange={(e) => setNotes((current) => ({ ...current, [line.id]: e.target.value }))}
              />
            </div>
          ))}

          {cartLines.length > 0 && (
            <>
              <div className="total">
                <span>Total</span>
                <span>{dollars(totalCents)}</span>
              </div>
              <button className="primary" onClick={placeOrder} disabled={placing}>
                {placing ? (hasNotes ? 'Reading your instructions…' : 'Placing order…') : 'Place order'}
              </button>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}

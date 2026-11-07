import { useState, useRef, useCallback } from 'react'
import { API_URL, dollars, usePolling } from './api.js'
import QrScanner from './QrScanner.jsx'

const statusLabel = { confirmed: 'Ready for pickup', picked_up: 'On the way' }

export default function Driver() {
  const drivers = usePolling('/drivers', 30000)
  const [pickedDriver, setPickedDriver] = useState('')
  const driverId = pickedDriver || drivers.data?.[0]?.id || null

  // Live data for the selected driver.
  const orders = usePolling(driverId ? `/drivers/${driverId}/orders` : null)
  const stats = usePolling(driverId ? `/drivers/${driverId}/stats` : null)
  const { reload: reloadOrders } = orders
  const { reload: reloadStats } = stats

  const [scanning, setScanning] = useState(false)
  const [manualCode, setManualCode] = useState('')
  const [message, setMessage] = useState({ text: '', ok: true })
  const lastScan = useRef({ code: '', time: 0 })

  const refresh = useCallback(() => {
    reloadOrders()
    reloadStats()
  }, [reloadOrders, reloadStats])

  // STEP 1: send the scanned code to the server to validate.
  const verifyPickup = useCallback(
    async (rawCode) => {
      const code = rawCode.trim()
      const now = Date.now()
      // The camera sees the same QR many times a second; ignore repeats for 4 seconds.
      if (!code || !driverId) return
      if (code === lastScan.current.code && now - lastScan.current.time < 4000) return
      lastScan.current = { code, time: now }
      try {
        const response = await fetch(`${API_URL}/pickup-scan`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ driver_id: driverId, qr_token: code }),
        })
        const data = await response.json()
        if (response.ok) {
          setMessage({ ok: true, text: `Order #${data.order_id} verified. You're good to go!` })
          setScanning(false)
          setManualCode('')
        } else {
          setMessage({ ok: false, text: typeof data.detail === 'string' ? data.detail : 'Scan failed.' })
        }
      } catch {
        setMessage({ ok: false, text: "Can't reach the server." })
      }
      refresh()
    },
    [driverId, refresh],
  )

  // STEP 2: confirm the hand-off at the customer's door.
  const confirmDropoff = async (orderId) => {
    try {
      const response = await fetch(`${API_URL}/orders/${orderId}/dropoff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_id: driverId }),
      })
      const data = await response.json()
      setMessage(
        response.ok
          ? { ok: true, text: `Order #${orderId} delivered. Nice work!` }
          : { ok: false, text: typeof data.detail === 'string' ? data.detail : 'Could not confirm.' },
      )
    } catch {
      setMessage({ ok: false, text: "Can't reach the server." })
    }
    refresh()
  }

  const myOrders = orders.data || []
  const myStats = stats.data
  const offline = orders.error || stats.error || drivers.error

  return (
    <div>
      <header>
        <h1>Driver Portal</h1>
        <p>A phone-sized screen. Scan the bag before leaving; confirm at the door.</p>
      </header>

      <div className="phone">
        {offline && <div className="banner error small">Can't reach the kitchen server.</div>}

        <label>
          Signed in as
          <select
            value={driverId || ''}
            onChange={(e) => { setPickedDriver(e.target.value); setMessage({ text: '', ok: true }); setScanning(false) }}
          >
            {(drivers.data || []).map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </label>

        {myStats && (
          <div className="stats">
            <div><strong>{myStats.deliveries_completed}</strong><span>Deliveries</span></div>
            <div><strong>{dollars(myStats.payout_cents)}</strong><span>Earned</span></div>
            <div><strong>{myStats.avg_delivery_seconds}s</strong><span>Avg time</span></div>
          </div>
        )}

        {message.text && <div className={`banner ${message.ok ? 'success' : 'error'} small`}>{message.text}</div>}

        <h2>Your deliveries</h2>
        {myOrders.length === 0 && <p className="empty">No active deliveries. Place an order in the storefront.</p>}
        {myOrders.map((o) => (
          <div key={o.id} className="delivery">
            <div className="feed-top">
              <strong>Order #{o.id}</strong>
              <span className={`chip ${o.status === 'confirmed' ? 'low' : 'ok'}`}>{statusLabel[o.status]}</span>
            </div>
            <div>{o.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}</div>
            {o.status === 'picked_up' && (
              <button className="primary" onClick={() => confirmDropoff(o.id)}>Confirm drop-off</button>
            )}
          </div>
        ))}

        <h2>Pick up a bag</h2>
        {scanning ? (
          <>
            <QrScanner onScan={verifyPickup} />
            <button onClick={() => setScanning(false)}>Stop camera</button>
          </>
        ) : (
          <button className="primary" onClick={() => setScanning(true)}>Scan bag QR code</button>
        )}

        <label className="manual">
          No camera? Paste the code from the receipt:
          <input type="text" value={manualCode} onChange={(e) => setManualCode(e.target.value)} placeholder="Bag code" />
        </label>
        <button onClick={() => verifyPickup(manualCode)} disabled={!manualCode.trim()}>Verify code</button>
      </div>
    </div>
  )
}

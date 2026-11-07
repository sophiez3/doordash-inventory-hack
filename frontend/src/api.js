import { useState, useEffect, useCallback } from 'react'

// Where your FastAPI server is running.
export const API_URL = 'http://127.0.0.1:8000'

// Prices are stored as whole cents (950), so convert for display ($9.50).
export const dollars = (cents) => `$${(cents / 100).toFixed(2)}`

// "beef_patty" -> "beef patty"
export const prettyName = (name) => name.replace(/_/g, ' ')

// { ingredient: "cheese_slice", delta: 1 } -> "+1 cheese slice"
export const modText = (m) => `${m.delta > 0 ? '+' : '−'}${Math.abs(m.delta)} ${prettyName(m.ingredient)}`

// The server stores times in UTC like "2026-11-05 14:03:09". Show them in local time.
export const localTime = (utcString) =>
  new Date(utcString.replace(' ', 'T') + 'Z').toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

// A reusable "hook": fetch `path` now and then again every `ms` milliseconds.
// Returns { data, error, reload }. Pass path = null to pause it.
export function usePolling(path, ms = 3000) {
  // We remember which path the data came from, so when `path` changes
  // (for example: a different driver is selected) old data is never shown.
  const [result, setResult] = useState({ path: null, data: null })
  const [error, setError] = useState(false)

  const reload = useCallback(async () => {
    if (!path) return
    try {
      const response = await fetch(`${API_URL}${path}`)
      if (!response.ok) throw new Error('Bad response')
      setResult({ path, data: await response.json() })
      setError(false)
    } catch {
      setError(true)
    }
  }, [path])

  useEffect(() => {
    reload()
    const timer = setInterval(reload, ms)
    return () => clearInterval(timer)
  }, [reload, ms])

  return { data: result.path === path ? result.data : null, error, reload }
}
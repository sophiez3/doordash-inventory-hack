import { useState } from 'react'
import Storefront from './Storefront.jsx'
import Kitchen from './Kitchen.jsx'
import Driver from './Driver.jsx'
import Insights from './Insights.jsx'
import './App.css'

// The app shell: four tabs, one view shown at a time.
export default function App() {
  const [view, setView] = useState('storefront')

  return (
    <div className="page">
      <nav className="tabs">
        <button className={view === 'storefront' ? 'active' : ''} onClick={() => setView('storefront')}>
          Customer Storefront
        </button>
        <button className={view === 'kitchen' ? 'active' : ''} onClick={() => setView('kitchen')}>
          Kitchen Dashboard
        </button>
        <button className={view === 'driver' ? 'active' : ''} onClick={() => setView('driver')}>
          Driver Portal
        </button>
        <button className={view === 'insights' ? 'active' : ''} onClick={() => setView('insights')}>
          Owner Insights
        </button>
      </nav>

      {view === 'storefront' && <Storefront />}
      {view === 'kitchen' && <Kitchen />}
      {view === 'driver' && <Driver />}
      {view === 'insights' && <Insights />}
    </div>
  )
}

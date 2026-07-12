import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { ConceptLanding } from './ConceptLanding'
import { ExpDashboard } from './ExpDashboard'
import { LocalRadarView } from './LocalRadarView'
import { MarketFitView } from './MarketFitView'
import { TrendsDashboard } from './TrendsDashboard'

// Tiny path-based router — the SPA fallback serves index.html for every route,
// so we pick the view from the pathname. No router dependency needed.
function Root() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path === '/you') return <MarketFitView />
  if (path === '/local') return <LocalRadarView />
  if (path === '/trends') return <TrendsDashboard />
  if (path === '/exp') return <ExpDashboard />
  if (path.startsWith('/c/')) return <ConceptLanding slug={path.slice(3)} />
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)

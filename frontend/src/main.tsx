import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { TrendsDashboard } from './TrendsDashboard'

// Tiny path-based router — the SPA fallback serves index.html for every route,
// so we pick the view from the pathname. No router dependency needed.
function Root() {
  const isTrends = window.location.pathname.replace(/\/+$/, '') === '/trends'
  return isTrends ? <TrendsDashboard /> : <App />
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)

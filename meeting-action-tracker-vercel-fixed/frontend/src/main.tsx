import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from '@/contexts/AuthContext'
import App from '@/App'
import './index.css'

// ── React Query client ────────────────────────────────────────────────────────

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Stale time: 30 seconds — avoids hammering the API on quick navigations
      staleTime: 30_000,
      // Retry once on failure (network hiccup tolerance)
      retry: 1,
      // Refetch on window focus to keep tracker data fresh
      refetchOnWindowFocus: true,
    },
    mutations: {
      // Surface mutation errors to the UI rather than silently retrying
      retry: 0,
    },
  },
})

// ── Global error handlers ─────────────────────────────────────────────────────

// C13: Surface unhandled promise rejections to the console so they are
// visible in monitoring tools and not silently swallowed by the browser.
window.addEventListener('unhandledrejection', (event) => {
  console.error('[unhandledrejection]', event.reason)
  // Prevent the browser from printing a separate uncaught-in-promise message
  event.preventDefault()
})

// ── Root mount ────────────────────────────────────────────────────────────────

const rootElement = document.getElementById('root')
if (rootElement === null) {
  throw new Error('Root element #root not found in index.html')
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)

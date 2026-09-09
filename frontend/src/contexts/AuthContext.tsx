import React, { createContext, useContext, type ReactNode } from 'react'
import { useAuthStore } from '@/store/auth'
import type { User } from '@/types'

// ── Context shape ─────────────────────────────────────────────────────────────

interface AuthContextValue {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  login: (token: string, user: User) => void
  logout: () => void
  setUser: (user: User) => void
}

// ── Context creation ──────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null)
AuthContext.displayName = 'AuthContext'

// ── Provider ──────────────────────────────────────────────────────────────────

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps): React.JSX.Element {
  // Bridge Zustand store into React context so consumers can use either mechanism.
  // Components that re-render frequently should prefer useAuthStore directly
  // with targeted selectors to avoid context-triggered re-renders.
  const user = useAuthStore((s) => s.user)
  const token = useAuthStore((s) => s.token)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const login = useAuthStore((s) => s.login)
  const logout = useAuthStore((s) => s.logout)
  const setUser = useAuthStore((s) => s.setUser)

  const value: AuthContextValue = {
    user,
    token,
    isAuthenticated,
    login,
    logout,
    setUser,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ── Hook ──────────────────────────────────────────────────────────────────────

/**
 * useAuth — returns the auth context value.
 * Must be called inside an <AuthProvider>.
 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}

export default AuthContext

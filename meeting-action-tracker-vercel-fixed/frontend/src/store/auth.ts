import { create } from 'zustand'
import type { User } from '@/types'

// ── Auth store state & actions ────────────────────────────────────────────────
// Token is kept in memory ONLY — never written to localStorage or sessionStorage.
// This prevents XSS-based token theft. The trade-off is that a hard refresh
// returns the user to the login page, which is intentional for this security posture.

interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  login: (token: string, user: User) => void
  logout: () => void
  setUser: (user: User) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  isAuthenticated: false,

  login: (token: string, user: User) => {
    set({ token, user, isAuthenticated: true })
  },

  logout: () => {
    set({ token: null, user: null, isAuthenticated: false })
  },

  setUser: (user: User) => {
    set({ user })
  },
}))

// Selector helpers (stable references, avoids unnecessary re-renders)
export const selectToken = (state: AuthState): string | null => state.token
export const selectUser = (state: AuthState): User | null => state.user
export const selectIsAuthenticated = (state: AuthState): boolean => state.isAuthenticated

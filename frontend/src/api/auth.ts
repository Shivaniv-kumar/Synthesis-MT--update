import { api } from '@/lib/api'
import type { User } from '@/types'

// ── Auth API ──────────────────────────────────────────────────────────────────

export interface LoginResponse {
  access_token: string
  user: User
}

/**
 * Authenticate with email/password. Returns JWT access token and user profile.
 */
export async function login(email: string, password: string): Promise<LoginResponse> {
  return api.post<LoginResponse>('/auth/login', { email, password })
}

/**
 * Fetch the currently authenticated user's profile.
 * Requires a valid Bearer token set on the axios instance.
 */
export async function getMe(): Promise<User> {
  return api.get<User>('/auth/me')
}

/**
 * Request a password reset email for the given address.
 * Always resolves — the server never reveals whether the email is registered.
 */
export async function forgotPassword(email: string): Promise<{ detail: string }> {
  return api.post<{ detail: string }>('/auth/forgot-password', { email })
}

/**
 * Complete the password reset with a one-time token from the reset email.
 */
export async function resetPassword(token: string, new_password: string): Promise<{ detail: string }> {
  return api.post<{ detail: string }>('/auth/reset-password', { token, new_password })
}

/**
 * Accept an invitation: set a password and activate the account.
 */
export async function acceptInvite(token: string, new_password: string): Promise<{ detail: string }> {
  return api.post<{ detail: string }>('/auth/accept-invite', { token, new_password })
}

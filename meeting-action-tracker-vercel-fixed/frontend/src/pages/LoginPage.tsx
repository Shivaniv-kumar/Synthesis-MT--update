import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import axios from 'axios'
import { useAuth } from '@/contexts/AuthContext'
import { login as loginApi } from '@/api/auth'

// ── Form data ─────────────────────────────────────────────────────────────────

interface LoginFormData {
  email: string
  password: string
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function LoginPage(): React.JSX.Element {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [apiError, setApiError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    defaultValues: { email: '', password: '' },
  })

  async function onSubmit(data: LoginFormData): Promise<void> {
    setApiError(null)
    try {
      const response = await loginApi(data.email, data.password)
      login(response.access_token, response.user)
      void navigate('/tracker', { replace: true })
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status
        const detail = err.response?.data?.detail as string | undefined
        if (!err.response) {
          setApiError('Cannot reach the server. Check the backend service and Vercel deployment logs.')
        } else if (status === 403) {
          setApiError(detail ?? 'Password login is disabled. Set ALLOW_PASSWORD_LOGIN=true in Vercel Environment Variables.')
        } else if (status === 404) {
          setApiError('API path not found. Verify that /api/* is routed to the backend service in vercel.json.')
        } else if (status === 401) {
          setApiError('Invalid credentials. Please check your email and password.')
        } else {
          setApiError(detail ?? `Unexpected error (${status ?? 'unknown'}). Check the Vercel backend logs and database connection.`)
        }
      } else {
        setApiError('Invalid credentials. Please check your email and password.')
      }
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-md">
        {/* Branding */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary-700">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-white" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z" />
              <path fillRule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-primary-900">Synthesis</h1>
          <p className="mt-1 text-sm text-primary-500">Sign in to your account</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
          {/* API error */}
          {apiError !== null && (
            <div
              role="alert"
              className="mb-5 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              {apiError}
            </div>
          )}

          <form onSubmit={(e) => void handleSubmit(onSubmit)(e)} noValidate className="space-y-5">
            {/* Email */}
            <div>
              <label htmlFor="email" className="mb-1.5 block text-sm font-medium text-primary-700">
                Email address
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                {...register('email', {
                  required: 'Email is required',
                  pattern: {
                    value: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
                    message: 'Enter a valid email address',
                  },
                })}
                aria-invalid={errors.email !== undefined ? 'true' : 'false'}
                aria-describedby={errors.email !== undefined ? 'email-error' : undefined}
                className={`w-full rounded-lg border px-3 py-2.5 text-sm text-primary-800 placeholder-primary-400 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 ${
                  errors.email !== undefined
                    ? 'border-red-300 bg-red-50 focus:border-red-400 focus:ring-red-400'
                    : 'border-slate-300 bg-white focus:border-primary-500'
                }`}
              />
              {errors.email !== undefined && (
                <p id="email-error" role="alert" className="mt-1.5 text-xs text-red-600">
                  {errors.email.message}
                </p>
              )}
            </div>

            {/* Password */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label htmlFor="password" className="text-sm font-medium text-primary-700">
                  Password
                </label>
                <Link to="/forgot-password" className="text-xs font-medium text-primary-600 hover:text-primary-900">
                  Forgot password?
                </Link>
              </div>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                {...register('password', {
                  required: 'Password is required',
                  minLength: { value: 6, message: 'Password must be at least 6 characters' },
                })}
                aria-invalid={errors.password !== undefined ? 'true' : 'false'}
                aria-describedby={errors.password !== undefined ? 'password-error' : undefined}
                className={`w-full rounded-lg border px-3 py-2.5 text-sm text-primary-800 placeholder-primary-400 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 ${
                  errors.password !== undefined
                    ? 'border-red-300 bg-red-50 focus:border-red-400 focus:ring-red-400'
                    : 'border-slate-300 bg-white focus:border-primary-500'
                }`}
              />
              {errors.password !== undefined && (
                <p id="password-error" role="alert" className="mt-1.5 text-xs text-red-600">
                  {errors.password.message}
                </p>
              )}
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full rounded-lg bg-primary-700 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="h-4 w-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Signing in…
                </span>
              ) : (
                'Sign in'
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

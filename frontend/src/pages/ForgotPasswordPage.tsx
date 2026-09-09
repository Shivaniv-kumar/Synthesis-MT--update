import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { forgotPassword } from '@/api/auth'

interface FormData {
  email: string
}

export default function ForgotPasswordPage(): React.JSX.Element {
  const [submitted, setSubmitted] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ defaultValues: { email: '' } })

  async function onSubmit(data: FormData): Promise<void> {
    setApiError(null)
    try {
      await forgotPassword(data.email)
      setSubmitted(true)
    } catch {
      setApiError('Something went wrong. Please try again.')
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
          <p className="mt-1 text-sm text-primary-500">Reset your password</p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
          {submitted ? (
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className="text-base font-semibold text-primary-900">Check your email</h2>
              <p className="mt-2 text-sm text-primary-500">
                If that address is registered, we've sent a reset link. It expires in 1 hour.
              </p>
              <Link
                to="/login"
                className="mt-6 inline-block text-sm font-medium text-primary-700 hover:text-primary-900"
              >
                Back to sign in
              </Link>
            </div>
          ) : (
            <>
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

              <p className="mb-5 text-sm text-primary-500">
                Enter the email address for your account and we'll send you a reset link.
              </p>

              <form onSubmit={(e) => void handleSubmit(onSubmit)(e)} noValidate className="space-y-5">
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
                      Sending…
                    </span>
                  ) : (
                    'Send reset link'
                  )}
                </button>
              </form>

              <p className="mt-5 text-center text-sm text-primary-500">
                <Link to="/login" className="font-medium text-primary-700 hover:text-primary-900">
                  Back to sign in
                </Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

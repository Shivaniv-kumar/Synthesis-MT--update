import React, { type ReactNode } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import Layout from '@/components/Layout'

// ── Page imports ──────────────────────────────────────────────────────────────
import LoginPage from '@/pages/LoginPage'
import ForgotPasswordPage from '@/pages/ForgotPasswordPage'
import ResetPasswordPage from '@/pages/ResetPasswordPage'
import AcceptInvitePage from '@/pages/AcceptInvitePage'
import CapturePage from '@/pages/CapturePage'
import ReviewPage from '@/pages/ReviewPage'
import TrackerPage from '@/pages/TrackerPage'
import DashboardPage from '@/pages/DashboardPage'
import MeetingStatusPage from '@/pages/MeetingStatusPage'
import AdminPage from '@/pages/AdminPage'
import SettingsPage from '@/pages/SettingsPage'
import ChatPage from '@/pages/ChatPage'
import KnowledgeBasePage from '@/pages/KnowledgeBasePage'

// ── Route guards ──────────────────────────────────────────────────────────────

interface GuardProps {
  children: ReactNode
}

function ProtectedLayout(): React.JSX.Element {
  const { isAuthenticated } = useAuth()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <Layout />
}

function AdminGuard({ children }: GuardProps): React.JSX.Element {
  const { user } = useAuth()
  if (user?.role !== 'Admin') return <Navigate to="/tracker" replace />
  return <>{children}</>
}

function NonViewerGuard({ children }: GuardProps): React.JSX.Element {
  const { user } = useAuth()
  if (user?.role === 'Viewer') return <Navigate to="/tracker" replace />
  return <>{children}</>
}

// ── Root redirect ─────────────────────────────────────────────────────────────

function RootRedirect(): React.JSX.Element {
  const { isAuthenticated } = useAuth()
  return <Navigate to={isAuthenticated ? '/tracker' : '/login'} replace />
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App(): React.JSX.Element {
  return (
    <Routes>
      {/* Root — redirect based on auth state */}
      <Route path="/" element={<RootRedirect />} />

      {/* Public routes */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/accept-invite" element={<AcceptInvitePage />} />

      {/* All authenticated routes share the Layout (sidebar + navbar) */}
      <Route element={<ProtectedLayout />}>
        <Route path="/capture" element={<NonViewerGuard><CapturePage /></NonViewerGuard>} />
        <Route path="/review/:meetingId" element={<NonViewerGuard><ReviewPage /></NonViewerGuard>} />
        <Route path="/tracker" element={<TrackerPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/meeting/:meetingId/status" element={<MeetingStatusPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/knowledge" element={<KnowledgeBasePage />} />

        {/* Admin-only routes */}
        <Route
          path="/admin"
          element={
            <AdminGuard>
              <AdminPage />
            </AdminGuard>
          }
        />
        <Route
          path="/settings"
          element={
            <AdminGuard>
              <SettingsPage />
            </AdminGuard>
          }
        />
      </Route>

      {/* Fallback — redirect unknown paths to root */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

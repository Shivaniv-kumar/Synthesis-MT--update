import React, { useRef, useState, useEffect } from 'react'
import { useLocation, NavLink } from 'react-router-dom'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { getMyWorkspaces, switchWorkspace } from '@/api/workspaces'

// ── Route → page title map ────────────────────────────────────────────────────

const PAGE_TITLES: Record<string, string> = {
  '/capture': 'Capture Meeting',
  '/tracker': 'Tracker',
  '/dashboard': 'Dashboard',
  '/admin': 'Admin Console',
  '/settings': 'Settings',
  '/chat': 'Meeting Assistant',
}

function getPageTitle(pathname: string): string {
  if (pathname.startsWith('/review/')) return 'Review Extracted Items'
  return PAGE_TITLES[pathname] ?? 'Synthesis'
}

function getInitials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? '').join('')
}

// ── Toolbar icon button ───────────────────────────────────────────────────────

interface ToolBtnProps {
  label: string
  children: React.ReactNode
  to?: string
  onClick?: () => void
  active?: boolean
}

function ToolBtn({ label, children, to, onClick, active }: ToolBtnProps): React.JSX.Element {
  const cls = `relative inline-flex items-center justify-center rounded-lg p-2 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400 ${
    active ? 'bg-primary-100 text-primary-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'
  }`
  if (to) {
    return (
      <NavLink to={to} aria-label={label} title={label} className={({ isActive }) =>
        `relative inline-flex items-center justify-center rounded-lg p-2 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400 ${
          isActive ? 'bg-primary-100 text-primary-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'
        }`
      }>
        {children}
      </NavLink>
    )
  }
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className={cls}>
      {children}
    </button>
  )
}

// ── Workspace switcher ────────────────────────────────────────────────────────

function WorkspaceSwitcher(): React.JSX.Element | null {
  const { user, login } = useAuth()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const { data: workspaces = [] } = useQuery({
    queryKey: ['my-workspaces'],
    queryFn: getMyWorkspaces,
    staleTime: 5 * 60 * 1000,
  })

  const switchMut = useMutation({
    mutationFn: (id: string) => switchWorkspace(id),
    onSuccess: (response) => {
      login(response.access_token, response.user)
      queryClient.clear()
      setOpen(false)
    },
  })

  // Close dropdown on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (workspaces.length <= 1) {
    const name = workspaces[0]?.name ?? 'Workspace'
    return (
      <span className="hidden max-w-[140px] truncate text-xs font-medium text-slate-500 sm:inline" title={name}>
        {name}
      </span>
    )
  }

  const currentName = workspaces.find((w) => w.id === user?.workspace_id)?.name ?? 'Workspace'

  return (
    <div ref={ref} className="relative hidden sm:block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-[160px] items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-primary-400"
        title="Switch workspace"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 flex-shrink-0 text-slate-400" viewBox="0 0 20 20" fill="currentColor">
          <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" />
        </svg>
        <span className="truncate">{currentName}</span>
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3 flex-shrink-0 text-slate-400" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-52 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
          <p className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Switch workspace
          </p>
          {workspaces.map((ws) => (
            <button
              key={ws.id}
              type="button"
              disabled={ws.id === user?.workspace_id || switchMut.isPending}
              onClick={() => switchMut.mutate(ws.id)}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-default disabled:opacity-60"
            >
              {ws.id === user?.workspace_id && (
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 flex-shrink-0 text-primary-600" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
              )}
              {ws.id !== user?.workspace_id && <span className="h-3.5 w-3.5 flex-shrink-0" />}
              <span className="truncate">{ws.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

interface NavbarProps {
  onMobileMenuToggle?: () => void
}

export default function Navbar({ onMobileMenuToggle }: NavbarProps): React.JSX.Element {
  const location = useLocation()
  const { user } = useAuth()
  const pageTitle = getPageTitle(location.pathname)

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-slate-200 bg-white px-4 shadow-sm sm:px-5">
      {/* Left: mobile toggle + breadcrumb */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onMobileMenuToggle}
          aria-label="Open navigation menu"
          className="inline-flex items-center justify-center rounded-md p-2 text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-primary-400 sm:hidden"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <span className="hidden text-xs font-medium text-slate-400 sm:inline">Synthesis</span>
        <span className="hidden text-slate-300 sm:inline">/</span>
        <WorkspaceSwitcher />
        <span className="hidden text-slate-300 sm:inline">/</span>
        <span className="text-sm font-semibold text-slate-700">{pageTitle}</span>
      </div>

      {/* Center: quick-nav icons (desktop) */}
      <div className="hidden items-center gap-1 sm:flex">
        <ToolBtn label="Tracker" to="/tracker">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5 h-[18px] w-[18px]" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z" />
            <path fillRule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd" />
          </svg>
        </ToolBtn>

        <ToolBtn label="Dashboard" to="/dashboard">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-[18px] w-[18px]" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" />
          </svg>
        </ToolBtn>

        {user?.role !== 'Viewer' && (
          <ToolBtn label="Capture Meeting" to="/capture">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-[18px] w-[18px]" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
          </ToolBtn>
        )}

        <div className="mx-2 h-5 w-px bg-slate-200" aria-hidden="true" />

        <ToolBtn label="Calendar (coming soon)" onClick={() => {}}>
          <svg xmlns="http://www.w3.org/2000/svg" className="h-[18px] w-[18px]" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
          </svg>
        </ToolBtn>

        <ToolBtn label="Documents (coming soon)" onClick={() => {}}>
          <svg xmlns="http://www.w3.org/2000/svg" className="h-[18px] w-[18px]" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
          </svg>
        </ToolBtn>
      </div>

      {/* Right: notifications + avatar + role badge */}
      <div className="flex items-center gap-2">
        <ToolBtn label="Notifications" onClick={() => {}}>
          <svg xmlns="http://www.w3.org/2000/svg" className="h-[18px] w-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
        </ToolBtn>

        {/* Role chip */}
        {user?.role && (
          <span className="hidden rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-500 sm:inline">
            {user.role}
          </span>
        )}

        {/* Avatar */}
        <div
          title={user?.display_name ?? 'User'}
          className="flex h-8 w-8 cursor-default select-none items-center justify-center rounded-full bg-gradient-to-br from-primary-700 to-primary-900 text-xs font-bold text-white shadow-sm"
          aria-label={`Logged in as ${user?.display_name ?? 'User'}`}
        >
          {user !== null ? getInitials(user.display_name) : '?'}
        </div>
      </div>
    </header>
  )
}

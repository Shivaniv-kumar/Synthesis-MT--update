import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { createUser, getMembers, inviteMember, updateMemberRole, removeMember } from '@/api/admin'
import type { CreateUserPayload } from '@/api/admin'
import { getMeetings } from '@/api/meetings'
import { getProjects } from '@/api/projects'
import type { WorkspaceMember, Meeting, Project } from '@/types'
import {
  getRules,
  createRule,
  toggleRule,
  deleteRule,
  getNotificationLog,
  testRule,
  runRule,
} from '@/api/notifications'
import type { NotificationRule, CreateRulePayload } from '@/api/notifications'

// ── Role badge ────────────────────────────────────────────────────────────────

function RoleBadge({ role }: { role: string }): React.JSX.Element {
  const colours: Record<string, string> = {
    Admin: 'bg-violet-100 text-violet-800',
    Member: 'bg-blue-100 text-blue-800',
    Viewer: 'bg-slate-100 text-slate-700',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${colours[role] ?? colours.Viewer}`}>
      {role}
    </span>
  )
}

// ── Create User Modal ─────────────────────────────────────────────────────────

interface CreateUserModalProps {
  workspaceId: string
  onClose: () => void
  onSuccess: () => void
}

function CreateUserModal({ workspaceId, onClose, onSuccess }: CreateUserModalProps): React.JSX.Element {
  const [form, setForm] = useState<CreateUserPayload & { confirmPassword: string }>({
    display_name: '',
    email: '',
    role: 'Member',
    password: '',
    confirmPassword: '',
    project_ids: [],
  })
  const [fieldError, setFieldError] = useState<string | null>(null)

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
    staleTime: 60_000,
  })

  const mutation = useMutation({
    mutationFn: (payload: CreateUserPayload) => createUser(workspaceId, payload),
    onSuccess: () => {
      onSuccess()
      onClose()
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      const msg = Array.isArray(detail)
        ? detail.map((e: any) => (typeof e === 'object' ? (e.msg ?? JSON.stringify(e)) : String(e))).join('; ')
        : typeof detail === 'string'
        ? detail
        : (err?.message ?? 'Failed to create user.')
      setFieldError(msg)
    },
  })

  function toggleProject(id: string): void {
    setForm((p) => {
      const ids = p.project_ids ?? []
      return {
        ...p,
        project_ids: ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
      }
    })
  }

  function handleSubmit(e: React.FormEvent): void {
    e.preventDefault()
    setFieldError(null)

    if (form.password.length < 8) {
      setFieldError('Password must be at least 8 characters.')
      return
    }
    if (form.password !== form.confirmPassword) {
      setFieldError('Passwords do not match.')
      return
    }

    const { confirmPassword: _cp, ...payload } = form
    mutation.mutate(payload)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-user-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <h2 id="create-user-title" className="text-base font-semibold text-slate-900">
            Create New User
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-primary-400"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4 px-6 py-5">
          <div>
            <label htmlFor="display_name" className="mb-1 block text-xs font-medium text-slate-700">
              Display Name
            </label>
            <input
              id="display_name"
              type="text"
              required
              value={form.display_name}
              onChange={(e) => setForm((p) => ({ ...p, display_name: e.target.value }))}
              placeholder="Jane Smith"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div>
            <label htmlFor="email" className="mb-1 block text-xs font-medium text-slate-700">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              required
              value={form.email}
              onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
              placeholder="jane@company.com"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div>
            <label htmlFor="role" className="mb-1 block text-xs font-medium text-slate-700">
              Role
            </label>
            <select
              id="role"
              value={form.role}
              onChange={(e) => setForm((p) => ({ ...p, role: e.target.value as WorkspaceMember['role'] }))}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="Admin">Admin — full access</option>
              <option value="Member">Member — create, edit, extract</option>
              <option value="Viewer">Viewer — read-only access</option>
            </select>
          </div>

          {/* Project access */}
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-700">
              Project Access <span className="font-normal text-slate-400">(optional)</span>
            </p>
            {projects.length === 0 ? (
              <p className="text-xs text-slate-400">No projects available in this workspace.</p>
            ) : (
              <div className="max-h-36 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-2 space-y-1">
                {projects.map((proj) => {
                  const checked = (form.project_ids ?? []).includes(proj.id)
                  return (
                    <label
                      key={proj.id}
                      className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-white"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleProject(proj.id)}
                        className="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-500"
                      />
                      <span
                        className="mr-1.5 inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full"
                        style={{ backgroundColor: proj.color ?? '#6366f1' }}
                      />
                      {proj.name}
                    </label>
                  )
                })}
              </div>
            )}
          </div>

          <div>
            <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-700">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              maxLength={128}
              value={form.password}
              onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
              placeholder="Min 8 characters"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div>
            <label htmlFor="confirmPassword" className="mb-1 block text-xs font-medium text-slate-700">
              Confirm Password
            </label>
            <input
              id="confirmPassword"
              type="password"
              required
              value={form.confirmPassword}
              onChange={(e) => setForm((p) => ({ ...p, confirmPassword: e.target.value }))}
              placeholder="Re-enter password"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          {fieldError && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{fieldError}</p>
          )}

          <div className="flex justify-end gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              {mutation.isPending ? 'Creating…' : 'Create User'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Users tab ─────────────────────────────────────────────────────────────────

interface UsersTabProps {
  workspaceId: string
  currentUserId: string
}

function UsersTab({ workspaceId, currentUserId }: UsersTabProps): React.JSX.Element {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [roleEditing, setRoleEditing] = useState<Record<string, string>>({})
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<WorkspaceMember['role']>('Member')
  const [inviteProjectScope, setInviteProjectScope] = useState<'all' | 'specific'>('all')
  const [inviteProjectIds, setInviteProjectIds] = useState<string[]>([])
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null)

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
    staleTime: 60_000,
  })

  const inviteMut = useMutation({
    mutationFn: () => inviteMember(
      workspaceId,
      inviteEmail.trim(),
      inviteRole,
      inviteProjectScope === 'specific' ? inviteProjectIds : [],
    ),
    onSuccess: (member) => {
      queryClient.invalidateQueries({ queryKey: ['members', workspaceId] })
      setInviteSuccess(
        member.invite_pending
          ? `Invitation sent to ${member.email}. They'll receive a link to set up their password.`
          : `${member.email} has been added to this workspace and notified by email.`
      )
      setInviteEmail('')
      setInviteProjectScope('all')
      setInviteProjectIds([])
      setInviteError(null)
      setTimeout(() => setInviteSuccess(null), 5000)
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setInviteError(typeof detail === 'string' ? detail : 'Failed to send invitation.')
    },
  })

  const { data: members = [], isLoading, isError } = useQuery({
    queryKey: ['members', workspaceId],
    queryFn: () => getMembers(workspaceId),
    enabled: !!workspaceId,
  })

  const roleChange = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: WorkspaceMember['role'] }) =>
      updateMemberRole(workspaceId, userId, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['members', workspaceId] }),
  })

  const removeMut = useMutation({
    mutationFn: (userId: string) => removeMember(workspaceId, userId),
    onSuccess: () => {
      setRemoveError(null)
      queryClient.invalidateQueries({ queryKey: ['members', workspaceId] })
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setRemoveError(typeof detail === 'string' ? detail : 'Failed to remove user. Please try again.')
    },
  })

  function getInitials(name: string): string {
    return name.split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? '').join('')
  }

  if (isLoading) {
    return <div className="py-12 text-center text-sm text-slate-500">Loading users…</div>
  }
  if (isError) {
    return <div className="py-12 text-center text-sm text-red-600">Failed to load users.</div>
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Invite by email */}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-1 text-sm font-semibold text-slate-700">Invite a team member</h2>
        <p className="mb-4 text-xs text-slate-500">
          They will receive an email with a link to set up their password and access the workspace.
        </p>
        <form
          onSubmit={(e) => { e.preventDefault(); if (inviteEmail.trim()) { setInviteError(null); setInviteSuccess(null); inviteMut.mutate() } }}
          className="flex flex-col gap-3"
        >
          <div className="flex gap-2">
            <input
              type="email"
              required
              placeholder="colleague@company.com"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as WorkspaceMember['role'])}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="Admin">Admin</option>
              <option value="Member">Member</option>
              <option value="Viewer">Viewer</option>
            </select>
          </div>
          {/* Project access */}
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-600">Project access</p>
            <div className="flex gap-4">
              <label className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700">
                <input
                  type="radio"
                  name="invite-project-scope"
                  checked={inviteProjectScope === 'all'}
                  onChange={() => { setInviteProjectScope('all'); setInviteProjectIds([]) }}
                  className="accent-primary-600"
                />
                All projects
              </label>
              <label className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700">
                <input
                  type="radio"
                  name="invite-project-scope"
                  checked={inviteProjectScope === 'specific'}
                  onChange={() => setInviteProjectScope('specific')}
                  className="accent-primary-600"
                />
                Specific projects
              </label>
            </div>
            {inviteProjectScope === 'specific' && (
              <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-2">
                {projects.length === 0 ? (
                  <p className="py-2 text-center text-xs text-slate-400">No projects in this workspace.</p>
                ) : (
                  projects.map((p) => (
                    <label key={p.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-white">
                      <input
                        type="checkbox"
                        checked={inviteProjectIds.includes(p.id)}
                        onChange={(e) =>
                          setInviteProjectIds((prev) =>
                            e.target.checked ? [...prev, p.id] : prev.filter((id) => id !== p.id)
                          )
                        }
                        className="accent-primary-600"
                      />
                      <span className="text-sm text-slate-700">{p.name}</span>
                    </label>
                  ))
                )}
              </div>
            )}
          </div>

          {inviteError && <p className="text-xs text-red-600">{inviteError}</p>}
          {inviteSuccess && <p className="text-xs text-emerald-600">{inviteSuccess}</p>}
          <button
            type="submit"
            disabled={inviteMut.isPending || !inviteEmail.trim() || (inviteProjectScope === 'specific' && inviteProjectIds.length === 0)}
            className="self-start rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {inviteMut.isPending ? 'Sending…' : 'Send invitation'}
          </button>
        </form>
      </div>

      {/* Remove error banner */}
      {removeError && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <svg xmlns="http://www.w3.org/2000/svg" className="mt-0.5 h-4 w-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          <span className="flex-1">{removeError}</span>
          <button type="button" onClick={() => setRemoveError(null)} className="ml-2 text-red-500 hover:text-red-700" aria-label="Dismiss">✕</button>
        </div>
      )}

      <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate-600">
          {members.length} {members.length === 1 ? 'user' : 'users'} in this workspace
        </p>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-400"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
          </svg>
          Create User
        </button>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        {members.length === 0 ? (
          <div className="py-10 text-center text-sm text-slate-500">No users yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">User</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Email</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Role</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((m) => {
                const editing = roleEditing[m.user_id]
                const isSelf = m.user_id === currentUserId
                return (
                  <tr key={m.user_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary-600 text-xs font-bold text-white">
                          {getInitials(m.display_name)}
                        </div>
                        <div>
                          <p className="font-medium text-slate-900">{m.display_name}</p>
                          {isSelf && (
                            <p className="text-xs text-slate-400">You</p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{m.email}</td>
                    <td className="px-4 py-3">
                      {editing !== undefined ? (
                        <div className="flex items-center gap-2">
                          <select
                            value={editing}
                            onChange={(e) =>
                              setRoleEditing((p) => ({ ...p, [m.user_id]: e.target.value }))
                            }
                            className="rounded border border-slate-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary-500"
                          >
                            <option value="Admin">Admin</option>
                            <option value="Member">Member</option>
                            <option value="Viewer">Viewer</option>
                          </select>
                          <button
                            type="button"
                            onClick={() => {
                              roleChange.mutate({
                                userId: m.user_id,
                                role: editing as WorkspaceMember['role'],
                              })
                              setRoleEditing((p) => {
                                const next = { ...p }
                                delete next[m.user_id]
                                return next
                              })
                            }}
                            className="rounded bg-primary-600 px-2 py-1 text-xs font-medium text-white hover:bg-primary-700"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              setRoleEditing((p) => {
                                const next = { ...p }
                                delete next[m.user_id]
                                return next
                              })
                            }
                            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <RoleBadge role={m.role} />
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            setRoleEditing((p) => ({ ...p, [m.user_id]: m.role }))
                          }
                          className="rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          title="Change role"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                          </svg>
                        </button>
                        {!isSelf && (
                          <button
                            type="button"
                            disabled={removeMut.isPending}
                            onClick={() => {
                              if (confirm(`Remove ${m.display_name} from this workspace?`)) {
                                setRemoveError(null)
                                removeMut.mutate(m.user_id)
                              }
                            }}
                            className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                            title="Remove user"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                              <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      </div>

      {showCreate && (
        <CreateUserModal
          workspaceId={workspaceId}
          onClose={() => setShowCreate(false)}
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ['members', workspaceId] })}
        />
      )}
    </div>
  )
}

// ── Notifications tab ─────────────────────────────────────────────────────────

const RULE_TYPE_LABELS: Record<string, string> = {
  assignment: 'Item Assigned',
  due_soon: 'Due Soon Alert',
  overdue: 'Overdue Alert',
  digest: 'Weekly Digest',
  sharing_summary: 'Sharing Summary',
}

const RULE_TYPE_DESC: Record<string, string> = {
  assignment: 'Sent when an action item is assigned to someone',
  due_soon: 'Sent daily for items due within 2 days',
  overdue: 'Sent daily for past-due open items',
  digest: 'Weekly summary of all open items (sent Mondays)',
  sharing_summary: 'Sends a formatted summary of action items to a channel on demand or on schedule',
}

const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email',
  slack: 'Slack',
  teams: 'Microsoft Teams',
}

const CHANNEL_COLOURS: Record<string, string> = {
  email: 'bg-blue-100 text-blue-700',
  slack: 'bg-green-100 text-green-700',
  teams: 'bg-purple-100 text-purple-700',
}

interface AddRuleModalProps {
  workspaceId: string
  onClose: () => void
  onSuccess: () => void
}

function AddRuleModal({ workspaceId, onClose, onSuccess }: AddRuleModalProps): React.JSX.Element {
  const [form, setForm] = useState<{
    rule_type: NotificationRule['rule_type']
    channel: NotificationRule['channel']
    webhook_url: string
    scoped_user_id: string
    scoped_user_display_name: string
    scoped_meeting_id: string
    scoped_meeting_title: string
    scoped_project_id: string
    scoped_project_name: string
    frequency: '' | 'daily' | 'weekly' | 'monthly'
    run_time: string
    run_day: string
  }>({ rule_type: 'assignment', channel: 'email', webhook_url: '', scoped_user_id: '', scoped_user_display_name: '', scoped_meeting_id: '', scoped_meeting_title: '', scoped_project_id: '', scoped_project_name: '', frequency: '', run_time: '09:00', run_day: 'Monday' })
  const [error, setError] = useState<string | null>(null)

  const { data: members = [] } = useQuery<WorkspaceMember[]>({
    queryKey: ['members', workspaceId],
    queryFn: () => getMembers(workspaceId),
    enabled: !!workspaceId,
  })

  const { data: meetingsPage } = useQuery({
    queryKey: ['meetings-list-for-rule'],
    queryFn: () => getMeetings({ page_size: 50 }),
  })
  const meetings: Meeting[] = meetingsPage?.items ?? []

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
    staleTime: 60_000,
  })

  const mutation = useMutation({
    mutationFn: (payload: CreateRulePayload) => createRule(payload),
    onSuccess: () => { onSuccess(); onClose() },
    onError: (e: Error) => setError(e.message || 'Failed to create rule.'),
  })

  function handleSubmit(e: React.FormEvent): void {
    e.preventDefault()
    setError(null)
    const needsWebhook = form.channel === 'slack' || form.channel === 'teams'
    if (needsWebhook && !form.webhook_url.trim()) {
      setError('Webhook URL is required for Slack and Teams.')
      return
    }
    const config: Record<string, unknown> = {}
    if (needsWebhook) config.webhook_url = form.webhook_url.trim()
    if (form.scoped_user_id) {
      config.user_id = form.scoped_user_id
      config.user_display_name = form.scoped_user_display_name
    }
    if (form.scoped_meeting_id) {
      config.meeting_id = form.scoped_meeting_id
      config.meeting_title = form.scoped_meeting_title
    }
    if (form.scoped_project_id) {
      config.project_id = form.scoped_project_id
      config.project_name = form.scoped_project_name
    }
    if (form.frequency) {
      config.frequency = form.frequency
      config.run_time = form.run_time || '09:00'
      if (form.frequency === 'weekly') config.run_day = form.run_day || 'Monday'
    }
    const payload: CreateRulePayload = {
      rule_type: form.rule_type,
      channel: form.channel,
      config: Object.keys(config).length > 0 ? config : undefined,
    }
    mutation.mutate(payload)
  }

  const needsWebhook = form.channel === 'slack' || form.channel === 'teams'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white shadow-2xl" role="dialog" aria-modal="true">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <h2 className="text-base font-semibold text-slate-900">Add Notification Rule</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 px-6 py-5">
          {/* Trigger */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">Trigger</label>
            <select
              value={form.rule_type}
              onChange={(e) => setForm((p) => ({ ...p, rule_type: e.target.value as NotificationRule['rule_type'] }))}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {Object.entries(RULE_TYPE_LABELS).map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-500">{RULE_TYPE_DESC[form.rule_type]}</p>
          </div>

          {/* Channel */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">Channel</label>
            <select
              value={form.channel}
              onChange={(e) => setForm((p) => ({ ...p, channel: e.target.value as NotificationRule['channel'] }))}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="email">Email</option>
              <option value="slack">Slack (webhook)</option>
              <option value="teams">Microsoft Teams (webhook)</option>
            </select>
          </div>

          {/* Webhook URL */}
          {needsWebhook && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">
                Incoming Webhook URL
              </label>
              <input
                type="url"
                required
                value={form.webhook_url}
                onChange={(e) => setForm((p) => ({ ...p, webhook_url: e.target.value }))}
                placeholder={form.channel === 'slack' ? 'https://hooks.slack.com/services/…' : 'https://outlook.office.com/webhook/…'}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
              <p className="mt-1 text-xs text-slate-500">
                {form.channel === 'slack'
                  ? 'Create an Incoming Webhook app in your Slack workspace settings.'
                  : 'Create an Incoming Webhook connector in your Teams channel settings.'}
              </p>
            </div>
          )}

          {/* User scope (optional) */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">
              Scope to User
              <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500">optional</span>
            </label>
            <select
              value={form.scoped_user_id}
              onChange={(e) => {
                const selected = members.find((m) => m.user_id === e.target.value)
                setForm((p) => ({
                  ...p,
                  scoped_user_id: e.target.value,
                  scoped_user_display_name: selected?.display_name ?? '',
                }))
              }}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="">All workspace users</option>
              {members.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.display_name} ({m.email})
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-500">
              When set, this rule only applies to items owned by the selected user.
            </p>
          </div>

          {/* Meeting scope — only relevant for sharing_summary */}
          {form.rule_type === 'sharing_summary' && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">
                Scope to Meeting
                <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500">optional</span>
              </label>
              <select
                value={form.scoped_meeting_id}
                onChange={(e) => {
                  const selected = meetings.find((m) => m.id === e.target.value)
                  setForm((p) => ({
                    ...p,
                    scoped_meeting_id: e.target.value,
                    scoped_meeting_title: selected?.title ?? '',
                  }))
                }}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              >
                <option value="">All meetings</option>
                {meetings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title}
                    {m.occurred_at != null ? ` — ${new Date(m.occurred_at).toLocaleDateString()}` : ''}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">
                When set, the summary will include items from this meeting only.
              </p>
            </div>
          )}

          {/* Project scope (optional) */}
          {projects.length > 0 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">
                Filter by Project
                <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500">optional</span>
              </label>
              <select
                value={form.scoped_project_id}
                onChange={(e) => {
                  const selected = projects.find((p) => p.id === e.target.value)
                  setForm((p) => ({
                    ...p,
                    scoped_project_id: e.target.value,
                    scoped_project_name: selected?.name ?? '',
                  }))
                }}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">
                When set, this rule only applies to items from the selected project.
              </p>
            </div>
          )}

          {/* Schedule */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">
              Schedule
              <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500">optional</span>
            </label>
            <div className="flex flex-wrap gap-2">
              <select
                value={form.frequency}
                onChange={(e) => setForm((p) => ({ ...p, frequency: e.target.value as typeof form.frequency }))}
                className="flex-1 min-w-[140px] rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              >
                <option value="">No schedule (manual only)</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
              {form.frequency && (
                <input
                  type="time"
                  value={form.run_time}
                  onChange={(e) => setForm((p) => ({ ...p, run_time: e.target.value }))}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              )}
              {form.frequency === 'weekly' && (
                <select
                  value={form.run_day}
                  onChange={(e) => setForm((p) => ({ ...p, run_day: e.target.value }))}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                >
                  {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              )}
            </div>
            {form.frequency && (
              <p className="mt-1 text-xs text-slate-500">
                {form.frequency === 'daily' && `Runs every day at ${form.run_time}`}
                {form.frequency === 'weekly' && `Runs every ${form.run_day} at ${form.run_time}`}
                {form.frequency === 'monthly' && `Runs on the 1st of each month at ${form.run_time}`}
              </p>
            )}
          </div>

          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Cancel</button>
            <button type="submit" disabled={mutation.isPending} className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50">
              {mutation.isPending ? 'Saving…' : 'Add Rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function NotificationsTab(): React.JSX.Element {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const workspaceId = user?.workspace_id ?? ''
  const [showAdd, setShowAdd] = useState(false)
  const [logOpen, setLogOpen] = useState(false)
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string } | null>>({})
  const [runResults, setRunResults] = useState<Record<string, { success: boolean; message: string } | null>>({})

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['notification-rules'],
    queryFn: getRules,
  })

  const { data: logs = [], isLoading: logsLoading } = useQuery({
    queryKey: ['notification-log'],
    queryFn: getNotificationLog,
    enabled: logOpen,
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => toggleRule(id, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-rules'] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteRule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-rules'] }),
  })

  const testMut = useMutation({
    mutationFn: (id: string) => testRule(id),
    onSuccess: (result, id) => {
      setTestResults((p) => ({ ...p, [id]: result }))
      setTimeout(() => setTestResults((p) => ({ ...p, [id]: null })), 6000)
    },
  })

  const runMut = useMutation({
    mutationFn: (id: string) => runRule(id),
    onSuccess: (result, id) => {
      setRunResults((p) => ({ ...p, [id]: result }))
      setTimeout(() => setRunResults((p) => ({ ...p, [id]: null })), 8000)
    },
  })

  function formatSchedule(config: Record<string, unknown>): string {
    const freq = config.frequency as string | undefined
    if (!freq) return ''
    const time = (config.run_time as string) || '09:00'
    const [h, m] = time.split(':')
    const d = new Date()
    d.setHours(Number(h), Number(m))
    const t = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    if (freq === 'daily') return `Daily at ${t}`
    if (freq === 'weekly') return `${config.run_day ?? 'Monday'}s at ${t}`
    if (freq === 'monthly') return `Monthly at ${t}`
    return ''
  }

  return (
    <div className="space-y-6">
      {/* Rules section */}
      <div>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Notification Rules</h3>
            <p className="text-xs text-slate-500">Configure when and where alerts are sent.</p>
          </div>
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-400"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
            Add Rule
          </button>
        </div>

        {rulesLoading ? (
          <div className="py-8 text-center text-sm text-slate-500">Loading rules…</div>
        ) : rules.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-200 py-12 text-center">
            <svg xmlns="http://www.w3.org/2000/svg" className="mx-auto mb-3 h-8 w-8 text-slate-300" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z" />
            </svg>
            <p className="text-sm font-medium text-slate-600">No notification rules yet</p>
            <p className="mt-1 text-xs text-slate-400">Add a rule to start sending alerts via email, Slack, or Teams.</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Trigger</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Channel</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                  <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <p className="font-medium text-slate-900">{RULE_TYPE_LABELS[rule.rule_type] ?? rule.rule_type}</p>
                      <p className="text-xs text-slate-400">{RULE_TYPE_DESC[rule.rule_type]}</p>
                      {rule.config?.user_id != null && (
                        <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700">
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                            <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
                          </svg>
                          {rule.config.user_display_name != null ? String(rule.config.user_display_name) : 'Scoped user'}
                        </span>
                      )}
                      {rule.config?.meeting_id != null && (
                        <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                            <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
                          </svg>
                          {rule.config.meeting_title != null ? String(rule.config.meeting_title) : 'Specific meeting'}
                        </span>
                      )}
                      {rule.config?.frequency != null && (
                        <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-slate-50 px-2 py-0.5 text-xs text-slate-500 border border-slate-200">
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clipRule="evenodd" />
                          </svg>
                          {formatSchedule(rule.config as Record<string, unknown>)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${CHANNEL_COLOURS[rule.channel] ?? 'bg-slate-100 text-slate-600'}`}>
                        {CHANNEL_LABELS[rule.channel] ?? rule.channel}
                      </span>
                      {rule.config?.webhook_url != null && (
                        <p className="mt-0.5 max-w-[200px] truncate text-xs text-slate-400" title={String(rule.config.webhook_url)}>
                          {String(rule.config.webhook_url)}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={rule.is_active}
                        onClick={() => toggleMut.mutate({ id: rule.id, active: !rule.is_active })}
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary-400 ${rule.is_active ? 'bg-primary-600' : 'bg-slate-300'}`}
                      >
                        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${rule.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </button>
                      <span className="ml-2 text-xs text-slate-500">{rule.is_active ? 'Active' : 'Paused'}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        {/* Test button */}
                        <button
                          type="button"
                          onClick={() => testMut.mutate(rule.id)}
                          disabled={testMut.isPending && testMut.variables === rule.id}
                          className="flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:border-primary-400 hover:bg-primary-50 hover:text-primary-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
                          title="Send a test notification now"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clipRule="evenodd" />
                          </svg>
                          {testMut.isPending && testMut.variables === rule.id ? 'Sending…' : 'Test'}
                        </button>
                        {/* Run button — sends real content immediately */}
                        <button
                          type="button"
                          onClick={() => runMut.mutate(rule.id)}
                          disabled={runMut.isPending && runMut.variables === rule.id}
                          className="flex items-center gap-1 rounded-lg border border-emerald-300 px-2.5 py-1.5 text-xs font-medium text-emerald-700 hover:border-emerald-400 hover:bg-emerald-50 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-emerald-400"
                          title="Send notification now with real action-item data"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
                          </svg>
                          {runMut.isPending && runMut.variables === rule.id ? 'Sending…' : 'Run'}
                        </button>
                        {/* Delete button */}
                        <button
                          type="button"
                          onClick={() => { if (confirm('Delete this notification rule?')) deleteMut.mutate(rule.id) }}
                          className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
                          title="Delete rule"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
                          </svg>
                        </button>
                      </div>
                      {/* Inline run result */}
                      {runResults[rule.id] != null && (
                        <p className={`mt-1.5 text-right text-xs font-medium ${runResults[rule.id]?.success ? 'text-emerald-600' : 'text-red-600'}`}>
                          {runResults[rule.id]?.success ? '✓' : '✗'} {runResults[rule.id]?.message}
                        </p>
                      )}
                      {/* Inline test result */}
                      {testResults[rule.id] != null && (
                        <p className={`mt-1.5 text-right text-xs font-medium ${testResults[rule.id]?.success ? 'text-green-600' : 'text-red-600'}`}>
                          {testResults[rule.id]?.success ? '✓' : '✗'} {testResults[rule.id]?.message}
                        </p>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Delivery log */}
      <div>
        <button
          type="button"
          onClick={() => setLogOpen((o) => !o)}
          className="flex items-center gap-2 text-sm font-semibold text-slate-700 hover:text-slate-900 focus:outline-none"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className={`h-4 w-4 transition-transform ${logOpen ? 'rotate-90' : ''}`} viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
          </svg>
          Delivery Log
        </button>

        {logOpen && (
          <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {logsLoading ? (
              <div className="py-8 text-center text-sm text-slate-500">Loading log…</div>
            ) : logs.length === 0 ? (
              <div className="py-8 text-center text-sm text-slate-500">No notifications sent yet.</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Type</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Channel</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Item</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Sent</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {logs.map((log) => (
                    <tr key={log.id} className="hover:bg-slate-50">
                      <td className="px-4 py-2.5 text-slate-700">{RULE_TYPE_LABELS[log.rule_type] ?? log.rule_type}</td>
                      <td className="px-4 py-2.5">
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${CHANNEL_COLOURS[log.channel] ?? 'bg-slate-100 text-slate-600'}`}>
                          {CHANNEL_LABELS[log.channel] ?? log.channel}
                        </span>
                      </td>
                      <td className="max-w-[220px] truncate px-4 py-2.5 text-xs text-slate-500" title={log.item_task ?? ''}>
                        {log.item_task ?? '—'}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-slate-500">
                        {new Date(log.sent_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2.5">
                        {log.status === 'sent' ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                            <span className="h-1.5 w-1.5 rounded-full bg-green-500" />Sent
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700" title={log.error ?? ''}>
                            <span className="h-1.5 w-1.5 rounded-full bg-red-500" />Failed
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {showAdd && (
        <AddRuleModal
          workspaceId={workspaceId}
          onClose={() => setShowAdd(false)}
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ['notification-rules'] })}
        />
      )}
    </div>
  )
}

// ── Settings Page ─────────────────────────────────────────────────────────────

type SettingsTab = 'users' | 'notifications'

export default function SettingsPage(): React.JSX.Element {
  const { user } = useAuth()
  const [activeTab, setActiveTab] = useState<SettingsTab>('users')

  if (user?.role !== 'Admin') {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <svg xmlns="http://www.w3.org/2000/svg" className="mb-4 h-12 w-12 text-slate-300" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd" />
        </svg>
        <h2 className="text-lg font-semibold text-slate-700">Admin Access Required</h2>
        <p className="mt-1 text-sm text-slate-500">Settings are only accessible to Admins.</p>
      </div>
    )
  }

  const workspaceId = user.workspace_id

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-xl font-bold text-slate-900">Settings</h1>
        <p className="mt-0.5 text-sm text-slate-500">Manage users and workspace configuration.</p>
      </div>

      {/* Tab bar */}
      <div className="border-b border-slate-200">
        <nav className="-mb-px flex gap-6" aria-label="Settings tabs">
          {(['users', 'notifications'] as SettingsTab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`border-b-2 pb-3 text-sm font-medium capitalize transition-colors focus:outline-none ${
                activeTab === tab
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700'
              }`}
            >
              {tab === 'notifications' ? 'Notifications' : 'Users'}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      {activeTab === 'users' && (
        <UsersTab workspaceId={workspaceId} currentUserId={user.id} />
      )}
      {activeTab === 'notifications' && (
        <NotificationsTab />
      )}
    </div>
  )
}

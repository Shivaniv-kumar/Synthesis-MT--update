import React, { useState, useRef, useEffect } from 'react'
import { sendChatMessage, shareResponse } from '@/api/chat'
import type { ChatHistoryMessage, ChatSource } from '@/api/chat'

// ── Message types ─────────────────────────────────────────────────────────────

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: ChatSource[]
  error?: boolean
}

// ── Message bubble ────────────────────────────────────────────────────────────

function UserBubble({ content }: { content: string }): React.JSX.Element {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary-600 px-4 py-3 text-sm text-white shadow-sm">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  )
}

function AssistantBubble({
  content,
  sources,
  error,
}: {
  content: string
  sources?: ChatSource[]
  error?: boolean
}): React.JSX.Element {
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)

  return (
    <div className="flex items-start gap-3">
      {/* Avatar */}
      <div className="mt-1 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 shadow-sm">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-white" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M9.504 1.132a1 1 0 01.992 0l1.75 1a1 1 0 11-.992 1.736L10 3.152l-1.254.716a1 1 0 11-.992-1.736l1.75-1zM5.618 4.504a1 1 0 01-.372 1.364L5.016 6l.23.132a1 1 0 11-.992 1.736L4 7.723V8a1 1 0 01-2 0V6a.996.996 0 01.52-.878l1.734-.99a1 1 0 011.364.372zm8.764 0a1 1 0 011.364-.372l1.733.99A1.002 1.002 0 0118 6v2a1 1 0 11-2 0v-.277l-.254.145a1 1 0 11-.992-1.736l.23-.132-.23-.132a1 1 0 01-.372-1.364zm-7 4a1 1 0 011.364-.372L10 8.848l1.254-.716a1 1 0 11.992 1.736L11 10.58V12a1 1 0 11-2 0v-1.42l-1.246-.712a1 1 0 01-.372-1.364zM3 11a1 1 0 011 1v1.42l1.246.712a1 1 0 11-.992 1.736l-1.75-1A1 1 0 012 14v-2a1 1 0 011-1zm14 0a1 1 0 011 1v2a1 1 0 01-.504.868l-1.75 1a1 1 0 11-.992-1.736L16 13.42V12a1 1 0 011-1zm-9.618 5.504a1 1 0 011.364-.372l.254.145V16a1 1 0 112 0v.277l.254-.145a1 1 0 11.992 1.736l-1.735.992a.995.995 0 01-1.022 0l-1.735-.992a1 1 0 01-.372-1.364z" clipRule="evenodd" />
        </svg>
      </div>

      <div className="flex-1 space-y-1.5">
        <div
          className={`rounded-2xl rounded-tl-sm px-4 py-3 text-sm shadow-sm ${
            error
              ? 'border border-red-200 bg-red-50 text-red-700'
              : 'bg-white text-slate-800'
          }`}
        >
          <p className="whitespace-pre-wrap leading-relaxed">{content}</p>
        </div>

        {/* Action row — sources + export */}
        <div className="flex items-center justify-between gap-4">
          {/* Sources toggle */}
          {sources && sources.length > 0 ? (
            <div>
              <button
                type="button"
                onClick={() => setSourcesOpen((o) => !o)}
                className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 focus:outline-none"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
                </svg>
                {sourcesOpen ? 'Hide' : 'Show'} {sources.length} source{sources.length !== 1 ? 's' : ''}
              </button>
              {sourcesOpen && (
                <ul className="mt-1 space-y-0.5">
                  {sources.map((s) => (
                    <li key={s.meeting_id} className="flex items-center gap-1.5 text-xs text-slate-500">
                      <span className="h-1 w-1 rounded-full bg-slate-300" aria-hidden="true" />
                      {s.meeting_title}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <span />
          )}

          {/* Export button — only for non-error messages */}
          {!error && (
            <button
              type="button"
              onClick={() => setShareOpen(true)}
              title="Export / Share this response"
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                <path d="M15 8a3 3 0 10-2.977-2.63l-4.94 2.47a3 3 0 100 4.319l4.94 2.47a3 3 0 10.895-1.789l-4.94-2.47a3.027 3.027 0 000-.74l4.94-2.47C13.456 7.68 14.19 8 15 8z" />
              </svg>
              Export
            </button>
          )}
        </div>
      </div>

      {shareOpen && (
        <ShareModal content={content} onClose={() => setShareOpen(false)} />
      )}
    </div>
  )
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function TypingIndicator(): React.JSX.Element {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 shadow-sm">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-white" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M9.504 1.132a1 1 0 01.992 0l1.75 1a1 1 0 11-.992 1.736L10 3.152l-1.254.716a1 1 0 11-.992-1.736l1.75-1zM5.618 4.504a1 1 0 01-.372 1.364L5.016 6l.23.132a1 1 0 11-.992 1.736L4 7.723V8a1 1 0 01-2 0V6a.996.996 0 01.52-.878l1.734-.99a1 1 0 011.364.372zm8.764 0a1 1 0 011.364-.372l1.733.99A1.002 1.002 0 0118 6v2a1 1 0 11-2 0v-.277l-.254.145a1 1 0 11-.992-1.736l.23-.132-.23-.132a1 1 0 01-.372-1.364zm-7 4a1 1 0 011.364-.372L10 8.848l1.254-.716a1 1 0 11.992 1.736L11 10.58V12a1 1 0 11-2 0v-1.42l-1.246-.712a1 1 0 01-.372-1.364zM3 11a1 1 0 011 1v1.42l1.246.712a1 1 0 11-.992 1.736l-1.75-1A1 1 0 012 14v-2a1 1 0 011-1zm14 0a1 1 0 011 1v2a1 1 0 01-.504.868l-1.75 1a1 1 0 11-.992-1.736L16 13.42V12a1 1 0 011-1zm-9.618 5.504a1 1 0 011.364-.372l.254.145V16a1 1 0 112 0v.277l.254-.145a1 1 0 11.992 1.736l-1.735.992a.995.995 0 01-1.022 0l-1.735-.992a1 1 0 01-.372-1.364z" clipRule="evenodd" />
        </svg>
      </div>
      <div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-2 w-2 rounded-full bg-slate-300"
              style={{ animation: `bounce 1.2s ease-in-out ${i * 0.2}s infinite` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Share modal ───────────────────────────────────────────────────────────────

interface ShareModalProps {
  content: string
  onClose: () => void
}

function ShareModal({ content, onClose }: ShareModalProps): React.JSX.Element {
  const [copied, setCopied] = useState(false)
  const [emailTarget, setEmailTarget] = useState('')
  const [teamsWebhook, setTeamsWebhook] = useState('')
  const [slackWebhook, setSlackWebhook] = useState('')
  const [result, setResult] = useState<{ channel: string; success: boolean; message: string } | null>(null)
  const [sending, setSending] = useState<string | null>(null)

  function handleCopy(): void {
    void navigator.clipboard.writeText(content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleDownload(): void {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'meeting-summary.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleShare(channel: 'email' | 'teams' | 'slack', target: string): Promise<void> {
    if (!target.trim()) return
    setSending(channel)
    setResult(null)
    try {
      const res = await shareResponse({ content, channel, target: target.trim() })
      setResult({ channel, success: res.success, message: res.message })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to send.'
      setResult({ channel, success: false, message: msg })
    } finally {
      setSending(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        className="w-full max-w-sm rounded-xl bg-white shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <h2 id="share-modal-title" className="text-sm font-semibold text-slate-900">Export Response</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-primary-400"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 px-5 py-5">
          {/* Quick actions */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleCopy}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              {copied ? (
                <>
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-green-600" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                  </svg>
                  <span className="text-green-600">Copied!</span>
                </>
              ) : (
                <>
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z" />
                    <path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z" />
                  </svg>
                  Copy text
                </>
              )}
            </button>
            <button
              type="button"
              onClick={handleDownload}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
              Download .md
            </button>
          </div>

          <div className="border-t border-slate-100" />

          {/* Send via Email */}
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-slate-700">Send via Email</label>
            <div className="flex gap-2">
              <input
                type="email"
                value={emailTarget}
                onChange={(e) => setEmailTarget(e.target.value)}
                placeholder="recipient@example.com"
                className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
              <button
                type="button"
                disabled={!emailTarget.trim() || sending === 'email'}
                onClick={() => void handleShare('email', emailTarget)}
                className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
              >
                {sending === 'email' ? '…' : 'Send'}
              </button>
            </div>
          </div>

          {/* Send to Teams */}
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-slate-700">Send to Microsoft Teams</label>
            <div className="flex gap-2">
              <input
                type="url"
                value={teamsWebhook}
                onChange={(e) => setTeamsWebhook(e.target.value)}
                placeholder="https://outlook.office.com/webhook/…"
                className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
              <button
                type="button"
                disabled={!teamsWebhook.trim() || sending === 'teams'}
                onClick={() => void handleShare('teams', teamsWebhook)}
                className="rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-purple-400"
              >
                {sending === 'teams' ? '…' : 'Send'}
              </button>
            </div>
          </div>

          {/* Send to Slack */}
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-slate-700">Send to Slack</label>
            <div className="flex gap-2">
              <input
                type="url"
                value={slackWebhook}
                onChange={(e) => setSlackWebhook(e.target.value)}
                placeholder="https://hooks.slack.com/services/…"
                className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
              <button
                type="button"
                disabled={!slackWebhook.trim() || sending === 'slack'}
                onClick={() => void handleShare('slack', slackWebhook)}
                className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-green-400"
              >
                {sending === 'slack' ? '…' : 'Send'}
              </button>
            </div>
          </div>

          {/* Result feedback */}
          {result != null && (
            <p className={`rounded-lg px-3 py-2 text-xs font-medium ${result.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {result.success ? '✓ ' : '✗ '}{result.message}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Suggested questions ───────────────────────────────────────────────────────

const SUGGESTED_QUESTIONS = [
  'What are the highest priority open action items?',
  'Which action items are overdue?',
  'Summarise the most recent meeting',
  'What items are assigned to me?',
]

function SuggestedQuestions({ onSelect }: { onSelect: (q: string) => void }): React.JSX.Element {
  return (
    <div className="space-y-3 text-center">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 shadow-md">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-7 w-7 text-white" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M18 10c0 3.866-3.582 7-8 7a8.841 8.841 0 01-4.083-.98L2 17l1.338-3.123C2.493 12.767 2 11.434 2 10c0-3.866 3.582-7 8-7s8 3.134 8 7zM7 9H5v2h2V9zm8 0h-2v2h2V9zM9 9h2v2H9V9z" clipRule="evenodd" />
        </svg>
      </div>
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Meeting Assistant</h2>
        <p className="mt-1 text-sm text-slate-500">
          Ask anything about your meeting transcripts and action items.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 pt-2 sm:grid-cols-2">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onSelect(q)}
            className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 shadow-sm transition-colors hover:border-primary-300 hover:bg-primary-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Chat page ─────────────────────────────────────────────────────────────────

let _idCounter = 0
function nextId(): string {
  _idCounter += 1
  return String(_idCounter)
}

export default function ChatPage(): React.JSX.Element {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-scroll to latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  function buildHistory(): ChatHistoryMessage[] {
    return messages
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content }))
  }

  async function handleSend(text: string): Promise<void> {
    const trimmed = text.trim()
    if (!trimmed || loading) return

    setInput('')
    setLoading(true)

    const userMsg: Message = { id: nextId(), role: 'user', content: trimmed }
    setMessages((prev) => [...prev, userMsg])

    try {
      const history = buildHistory()
      const res = await sendChatMessage(trimmed, history)
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          content: res.reply,
          sources: res.sources,
        },
      ])
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          content: 'Sorry, I encountered an error. Please try again in a moment.',
          error: true,
        },
      ])
    } finally {
      setLoading(false)
      textareaRef.current?.focus()
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend(input)
    }
  }

  function handleSuggestion(q: string): void {
    void handleSend(q)
  }

  return (
    <div className="flex h-full flex-col" style={{ height: 'calc(100vh - 56px)' }}>
      {/* Page header */}
      <div className="flex-shrink-0 border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-base font-bold text-slate-900">Meeting Assistant</h1>
            <p className="text-xs text-slate-500">Ask questions about your meeting transcripts and action items</p>
          </div>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={() => setMessages([])}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary-400"
            >
              Clear chat
            </button>
          )}
        </div>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto bg-slate-50 px-4 py-6">
        <div className="mx-auto max-w-2xl space-y-4">
          {messages.length === 0 && !loading ? (
            <SuggestedQuestions onSelect={handleSuggestion} />
          ) : (
            <>
              {messages.map((m) =>
                m.role === 'user' ? (
                  <UserBubble key={m.id} content={m.content} />
                ) : (
                  <AssistantBubble
                    key={m.id}
                    content={m.content}
                    sources={m.sources}
                    error={m.error}
                  />
                ),
              )}
              {loading && <TypingIndicator />}
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input bar */}
      <div className="flex-shrink-0 border-t border-slate-200 bg-white px-4 py-3">
        <div className="mx-auto max-w-2xl">
          <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white px-4 py-2 shadow-sm focus-within:border-primary-400 focus-within:ring-1 focus-within:ring-primary-400">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your meetings… (Enter to send, Shift+Enter for newline)"
              disabled={loading}
              className="flex-1 resize-none bg-transparent text-sm text-slate-800 placeholder-slate-400 focus:outline-none disabled:opacity-60"
              style={{ maxHeight: '120px', overflowY: 'auto' }}
            />
            <button
              type="button"
              onClick={() => void handleSend(input)}
              disabled={!input.trim() || loading}
              className="mb-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-primary-600 text-white transition-colors hover:bg-primary-700 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-primary-400"
              aria-label="Send message"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
              </svg>
            </button>
          </div>
          <p className="mt-1.5 text-center text-xs text-slate-400">
            Responses are based on your uploaded meeting transcripts and extracted action items.
          </p>
        </div>
      </div>

      {/* Bounce keyframe */}
      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: scale(0.8); opacity: 0.5; }
          40% { transform: scale(1.2); opacity: 1; }
        }
      `}</style>
    </div>
  )
}

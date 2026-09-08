// Re-exports the shared axios instance from lib/api.ts under the api/client path.
// The actual implementation lives in lib/api.ts to avoid duplicating interceptor logic.
// All api/* modules should import from this file, not from lib/api directly.

export { api as default, api } from '@/lib/api'

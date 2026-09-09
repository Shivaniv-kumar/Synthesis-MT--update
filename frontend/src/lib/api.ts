import axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { useAuthStore } from '@/store/auth'

// ── Base URL ──────────────────────────────────────────────────────────────────

const API_BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) || '/api'

// ── Axios instance ────────────────────────────────────────────────────────────

const axiosInstance: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  },
  timeout: 30_000,
  // FastAPI expects repeated params for arrays: ?category=a&category=b
  // Axios default serializes as ?category[]=a&category[]=b which FastAPI ignores.
  paramsSerializer: {
    serialize: (params: Record<string, unknown>) => {
      const sp = new URLSearchParams()
      for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null) continue
        if (Array.isArray(value)) {
          for (const item of value) sp.append(key, String(item))
        } else {
          sp.append(key, String(value))
        }
      }
      return sp.toString()
    },
  },
})

// ── Request interceptor — attach Bearer token ─────────────────────────────────

axiosInstance.interceptors.request.use(
  (config: InternalAxiosRequestConfig): InternalAxiosRequestConfig => {
    const token = useAuthStore.getState().token
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error: unknown) => Promise.reject(error),
)

// ── Response interceptor — handle 401 ────────────────────────────────────────

axiosInstance.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      // Don't redirect when already on the login page — avoids an infinite reload
      // that wipes the Network tab and swallows the real error.
      if (!window.location.pathname.startsWith('/login')) {
        useAuthStore.getState().logout()
        sessionStorage.setItem('session_expired', '1')
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

// ── Typed helper functions ────────────────────────────────────────────────────

/**
 * HTTP GET — returns the typed response body.
 */
async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await axiosInstance.get<T>(url, config)
  return response.data
}

/**
 * HTTP POST — sends a body and returns the typed response body.
 */
async function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await axiosInstance.post<T>(url, data, config)
  return response.data
}

/**
 * HTTP PATCH — sends a partial body and returns the typed response body.
 */
async function patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await axiosInstance.patch<T>(url, data, config)
  return response.data
}

/**
 * HTTP DELETE — returns the typed response body (or void for 204).
 */
async function del<T = void>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await axiosInstance.delete<T>(url, config)
  return response.data
}

// ── Exports ───────────────────────────────────────────────────────────────────

export const api = {
  get,
  post,
  patch,
  del,
  /** Raw axios instance for edge-case usage (e.g., file uploads with onUploadProgress) */
  instance: axiosInstance,
}

export default api

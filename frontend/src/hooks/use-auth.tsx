import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"
import { auth } from "@/lib/api"

interface AuthState {
  authenticated: boolean
  username: string | null
  csrfToken: string | null
  loading: boolean
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshCsrf: () => Promise<string>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    authenticated: false,
    username: null,
    csrfToken: null,
    loading: true,
  })

  // Check session on mount
  useEffect(() => {
    auth
      .me()
      .then((res) => {
        return auth.csrf().then((csrf) => {
          setState({
            authenticated: true,
            username: res.username,
            csrfToken: csrf.csrf_token,
            loading: false,
          })
        })
      })
      .catch(() => {
        setState({
          authenticated: false,
          username: null,
          csrfToken: null,
          loading: false,
        })
      })
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await auth.login(username, password)
    setState({
      authenticated: true,
      username: res.username,
      csrfToken: res.csrf_token,
      loading: false,
    })
  }, [])

  const logout = useCallback(async () => {
    await auth.logout()
    setState({
      authenticated: false,
      username: null,
      csrfToken: null,
      loading: false,
    })
  }, [])

  const refreshCsrf = useCallback(async () => {
    const res = await auth.csrf()
    setState((prev) => ({ ...prev, csrfToken: res.csrf_token }))
    return res.csrf_token
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, login, logout, refreshCsrf }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}

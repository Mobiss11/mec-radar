import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { AuthProvider, useAuth } from "@/hooks/use-auth"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DashboardLayout } from "@/layouts/dashboard-layout"
import { LoginPage } from "@/pages/login"
import { OverviewPage } from "@/pages/overview"
import { TokensPage } from "@/pages/tokens"
import { SignalsPage } from "@/pages/signals-page"
import { PortfolioPage } from "@/pages/portfolio-page"
import { AnalyticsPage } from "@/pages/analytics-page"
import { SettingsPage } from "@/pages/settings-page"
import type { ReactNode } from "react"

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (!authenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function PublicRoute({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (authenticated) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <TooltipProvider>
          <Routes>
            <Route
              path="/login"
              element={
                <PublicRoute>
                  <LoginPage />
                </PublicRoute>
              }
            />
            <Route
              element={
                <ProtectedRoute>
                  <DashboardLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<OverviewPage />} />
              <Route path="tokens" element={<TokensPage />} />
              <Route path="signals" element={<SignalsPage />} />
              <Route path="portfolio" element={<PortfolioPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </TooltipProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}

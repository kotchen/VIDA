import { Navigate, Route, Routes } from "react-router"
import { AppShell } from "./components/layout/AppShell"
import { DashboardPage } from "./pages/DashboardPage"
import { PlaceholderPage } from "./pages/PlaceholderPage"
import { SettingsPage } from "./pages/SettingsPage"

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/transcribe" element={<PlaceholderPage title="Transcribe" />} />
        <Route path="/library" element={<PlaceholderPage title="Library" />} />
        <Route path="/summaries" element={<PlaceholderPage title="Summaries" />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}

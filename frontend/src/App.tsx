import { Navigate, Route, Routes } from "react-router"
import { AppShell } from "./components/layout/AppShell"
import { DashboardPage } from "./pages/DashboardPage"
import { SettingsPage } from "./pages/SettingsPage"
import { TranscribePage } from "./pages/TranscribePage"
import { EpisodePage } from "./pages/EpisodePage"
import { LibraryPage } from "./pages/LibraryPage"
import { SummariesPage } from "./pages/SummariesPage"

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/transcribe" element={<TranscribePage />} />
        <Route path="/episodes/:id" element={<EpisodePage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/summaries" element={<SummariesPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}

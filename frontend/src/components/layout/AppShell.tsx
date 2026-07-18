import { Outlet } from "react-router"
import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"

export function AppShell() {
  return (
    <div className="min-h-screen bg-page">
      <div className="hidden min-[1280px]:flex">
        <Sidebar />
        <div className="flex min-h-screen flex-1 flex-col">
          <TopBar />
          <main className="flex-1">
            <Outlet />
          </main>
        </div>
      </div>
      <div className="flex min-h-screen items-center justify-center p-8 min-[1280px]:hidden">
        <p className="text-center text-sm text-muted-warm">
          VIDA 2.0 is best experienced on desktop.
        </p>
      </div>
    </div>
  )
}

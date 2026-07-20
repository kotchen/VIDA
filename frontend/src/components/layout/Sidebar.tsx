import { FileText, LayoutDashboard, Library, Mic, Settings } from "lucide-react"
import { NavLink } from "react-router"
import splashUrl from "../../assets/splash-sidebar.png"

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/transcribe", label: "Transcribe", icon: Mic },
  { to: "/library", label: "Library", icon: Library },
  { to: "/summaries", label: "Summaries", icon: FileText },
  { to: "/settings", label: "Settings", icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="relative flex h-screen w-[280px] shrink-0 flex-col overflow-hidden border-r border-warm/60 bg-sidebar">
      <div className="px-6 pb-8 pt-7">
        <div className="font-display text-4xl tracking-wide">
          <span className="text-gold-gradient">VIDA</span>
        </div>
        <p className="mt-1 text-xs text-muted-warm">Video Intelligence, Dialogue, Analysis</p>
      </div>
      <nav className="flex flex-col gap-1 px-3">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors ${
                isActive
                  ? "bg-copper-gradient font-semibold text-on-copper"
                  : "text-muted-warm hover:bg-raised hover:text-cream"
              }`
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto">
        <img src={splashUrl} alt="" className="h-44 w-full object-cover" />
        <p className="px-6 pb-5 pt-3 text-xs leading-relaxed text-muted-warm">
          Fueling insights,
          <br />
          one conversation at a time.
        </p>
      </div>
    </aside>
  )
}

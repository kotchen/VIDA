import { Search } from "lucide-react"

export function TopBar() {
  return (
    <header className="flex h-14 shrink-0 items-center justify-center border-b border-warm/60 px-6">
      <div className="flex w-full max-w-xl items-center gap-2 rounded-xl border border-warm/60 bg-card px-4 py-2 text-muted-warm">
        <Search className="size-4 shrink-0" />
        <input
          className="w-full bg-transparent text-sm text-cream outline-none placeholder:text-muted-warm"
          placeholder="Search projects, transcripts, summaries..."
        />
        <kbd className="shrink-0 rounded-md border border-warm/60 px-1.5 py-0.5 text-[10px]">⌘K</kbd>
      </div>
    </header>
  )
}

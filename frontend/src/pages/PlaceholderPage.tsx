import { Sparkles } from "lucide-react"

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col items-center justify-center gap-3">
      <Sparkles className="size-8 text-gold" />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-warm">Coming in VIDA 2.x</p>
    </div>
  )
}

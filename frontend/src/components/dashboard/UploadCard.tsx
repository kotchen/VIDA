import { CloudUpload } from "lucide-react"
import { Card } from "@/components/ui/card"
import { useRef, type DragEvent } from "react"

interface UploadCardProps {
  onFileSelected?: (file: File) => void
  disabled?: boolean
  help?: string
}

export function UploadCard({
  onFileSelected,
  disabled = false,
  help = "MP3, MP4, M4A, WAV, WEBM, MKV, OGG, FLAC · Up to 5GB",
}: UploadCardProps) {
  const input = useRef<HTMLInputElement>(null)
  const select = (file?: File) => {
    if (!disabled && file) onFileSelected?.(file)
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    select(event.dataTransfer.files[0])
  }

  return (
    <Card
      className="card-glow flex h-full cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-copper-500/50 bg-card p-6 text-center"
      onClick={() => !disabled && input.current?.click()}
      onDragOver={(event) => event.preventDefault()}
      onDrop={drop}
      aria-disabled={disabled}
    >
      <CloudUpload className="size-10 text-copper-300" strokeWidth={1.5} />
      <p className="text-base font-semibold">Upload a video or audio file</p>
      <p className="text-sm text-muted-warm">Drag &amp; drop a file here, or click to browse</p>
      <p className="text-xs text-muted-warm/70">{help}</p>
      <input
        ref={input}
        className="sr-only"
        type="file"
        aria-label="Quick upload media file"
        accept=".mp3,.mp4,.m4a,.wav,.webm,.mkv,.ogg,.flac"
        disabled={disabled}
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => select(event.target.files?.[0])}
      />
    </Card>
  )
}

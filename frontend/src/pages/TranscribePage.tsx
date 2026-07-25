import { SubmissionForm } from "@/features/submission/SubmissionForm"

export function TranscribePage() {
  return (
    <div className="mx-auto max-w-4xl p-8">
      <h1 className="font-display text-3xl text-gold">Transcribe media</h1>
      <p className="mb-6 mt-1 text-sm text-muted-warm">
        Upload a supported file or submit a public media URL.
      </p>
      <SubmissionForm />
    </div>
  )
}

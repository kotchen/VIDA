const STORAGE_KEY = "vida.v2.submissionPreferences"

export interface SubmissionPreferences {
  providerProfileId: string | null
  summaryLanguage: string
}

const DEFAULT_PREFERENCES: SubmissionPreferences = {
  providerProfileId: null,
  summaryLanguage: "zh",
}

export function getSubmissionPreferences(): SubmissionPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as unknown
    if (
      value !== null &&
      typeof value === "object" &&
      "providerProfileId" in value &&
      "summaryLanguage" in value &&
      ((value as { providerProfileId: unknown }).providerProfileId === null ||
        typeof (value as { providerProfileId: unknown }).providerProfileId ===
          "string") &&
      typeof (value as { summaryLanguage: unknown }).summaryLanguage === "string" &&
      (value as { summaryLanguage: string }).summaryLanguage.length > 0
    ) {
      return value as SubmissionPreferences
    }
  } catch {
    // Invalid user storage falls back to safe defaults.
  }
  return { ...DEFAULT_PREFERENCES }
}

export function setSubmissionPreferences(
  preferences: SubmissionPreferences,
): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences))
}

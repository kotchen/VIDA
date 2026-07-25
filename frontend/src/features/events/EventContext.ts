import { createContext, useContext } from "react"

import type { V2ClientEvent } from "@/api/events"

export type EventConnectionState = "connecting" | "open" | "closed"
export type EventSubscriber = (event: V2ClientEvent) => void

export interface EventContextValue {
  state: EventConnectionState
  hasOpened: boolean
  subscribe: (subscriber: EventSubscriber) => () => void
}

export const EventContext = createContext<EventContextValue | null>(null)

export function useEventContext(): EventContextValue {
  const context = useContext(EventContext)
  if (context === null) {
    throw new Error("Event hooks must be used inside EventProvider")
  }
  return context
}

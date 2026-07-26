import { BrowserRouter } from "react-router"
import type { ReactNode } from "react"

export const V2_ROUTER_BASENAME = "/v2"

export function V2BrowserRouter({ children }: { children: ReactNode }) {
  return (
    <BrowserRouter basename={V2_ROUTER_BASENAME}>
      {children}
    </BrowserRouter>
  )
}

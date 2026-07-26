import React from "react"
import ReactDOM from "react-dom/client"
import { EventProvider } from "@/features/events/EventProvider"
import { V2BrowserRouter } from "@/router"
import App from "./App"
import "@fontsource/dm-serif-display/400.css"
import "@fontsource/manrope/400.css"
import "@fontsource/manrope/500.css"
import "@fontsource/manrope/600.css"
import "@fontsource/manrope/700.css"
import "./theme/tokens.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <EventProvider>
      <V2BrowserRouter>
        <App />
      </V2BrowserRouter>
    </EventProvider>
  </React.StrictMode>
)

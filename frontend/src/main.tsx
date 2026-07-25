import React from "react"
import ReactDOM from "react-dom/client"
import { BrowserRouter } from "react-router"
import { EventProvider } from "@/features/events/EventProvider"
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
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </EventProvider>
  </React.StrictMode>
)

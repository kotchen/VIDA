# VIDA 2.0 Frontend

Video Intelligence, Dialogue, Analysis — the VIDA 2.0 web frontend.

**Stack:** React 19 + TypeScript + Vite + Tailwind CSS v4 (CSS-first theming) + shadcn/ui (new-york).

## Commands

```bash
npm ci          # install locked dependencies
npm run dev     # Vite + /api proxy → http://localhost:7100/v2/
npm test        # run tests (vitest)
npm run build   # production build
```

## Notes

Production assets are built with base `/v2/` and served by FastAPI. The UI uses
relative `/api/v2/*` requests and one `/api/v2/events` EventSource. See
`docs/api/v2-api-contract.md` at the repository root.

# VIDA Devnote and Startup Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the VIDA v2 frontend integration, establish repository-wide devnote and startup rules, then start and verify both development services.

**Architecture:** Keep project-wide workflow and startup commands in a new root `AGENTS.md`, while retaining `backend/v2/AGENTS.md` for v2-specific engineering constraints. Store the development summary as a dated, descriptively named Markdown document and run the existing backend and Vite entry points without adding wrapper scripts.

**Tech Stack:** Markdown, Git, Python 3.12 virtual environment, FastAPI/Uvicorn, Node.js/npm, Vite 8, curl.

## Global Constraints

- Create the devnote at `docs/devnote/2026-07-0725/vida-v2-frontend-integration.md`.
- Every devnote must include development content, learned/reusable experience, and rollback instructions.
- Add a root `AGENTS.md` rule requiring a new devnote after every development change.
- Document backend port `8000` and frontend port `7100`.
- Do not add a startup wrapper script or modify application runtime behavior.
- Run only one FastAPI/Uvicorn process.
- Never commit `VIDA_PROFILE_MASTER_KEY` or Provider API keys.

---

### Task 1: Write the dated development note

**Files:**
- Create: `docs/devnote/2026-07-0725/vida-v2-frontend-integration.md`

**Interfaces:**
- Consumes: commits `4e77170` through `928c460`, the v2 API contract, and the approved design.
- Produces: the durable human-readable record referenced by repository contributors.

- [ ] **Step 1: Create the devnote**

Write a Chinese Markdown document with these exact top-level sections:

```markdown
# VIDA v2 前端集成开发记录

## 1. 开发内容
## 2. 学习与可沉淀经验
## 3. 回滚操作
```

Cover the typed v2 client, Provider Profile management, Episode submission and lifecycle, SSE invalidation with polling fallback, terminal Episode deletion, content actions, page integration, production hosting, tests, and the incomplete Docker smoke check.

- [ ] **Step 2: Verify required sections and formatting**

Run:

```bash
rg -n "^## (1\\. 开发内容|2\\. 学习与可沉淀经验|3\\. 回滚操作)$" docs/devnote/2026-07-0725/vida-v2-frontend-integration.md
git diff --check
```

Expected: all three headings are present and `git diff --check` exits successfully.

### Task 2: Add repository-wide contributor and startup guidance

**Files:**
- Create: `AGENTS.md`

**Interfaces:**
- Consumes: `README_ZH.md`, `frontend/README.md`, `frontend/vite.config.ts`, `start.py`, and `backend/v2/AGENTS.md`.
- Produces: root-scoped instructions inherited by all repository work.

- [ ] **Step 1: Write root guidance**

Document:

```text
Backend: source venv/bin/activate; load .env when present; python start.py --prod
Frontend: cd frontend; npm ci; npm run dev -- --host 127.0.0.1
Ports: backend 8000, frontend 7100
URLs: /, /v2/, /api/v2/dashboard, /api/v2/events
Stop: Ctrl+C in each terminal
```

State that `VIDA_PROFILE_MASTER_KEY` is required for v2, must decode to 32 bytes, must remain stable with `data/v2/`, and must not be committed. Preserve the single-process backend constraint.

Add this mandatory rule:

```text
Every development change must add one descriptively named Markdown devnote under
docs/devnote/YYYY-MM-MMDD/. It must contain development content, learned/reusable
experience, and rollback instructions. Update an existing note only when continuing
the same development topic on the same date.
```

- [ ] **Step 2: Verify guidance**

Run:

```bash
rg -n "docs/devnote/YYYY-MM-MMDD|8000|7100|VIDA_PROFILE_MASTER_KEY|npm run dev|start.py --prod" AGENTS.md
git diff --check
```

Expected: every required rule and startup value is found.

### Task 3: Start and verify the backend

**Files:**
- Runtime only: `data/v2/`, `/private/tmp/vida-backend.log`

**Interfaces:**
- Consumes: `venv/bin/python`, `start.py`, generated ephemeral master key.
- Produces: one FastAPI/Uvicorn process listening on `127.0.0.1:8000`.

- [ ] **Step 1: Check whether port 8000 is already serving VIDA**

Run:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/api/v2/dashboard
```

Expected: reuse the existing service only if it returns a valid dashboard response; otherwise identify and resolve an owned stale VIDA process before starting.

- [ ] **Step 2: Start the backend**

Generate a temporary URL-safe Base64 32-byte key in the shell environment, without writing it to Git, and run:

```bash
venv/bin/python start.py --prod
```

Capture logs in `/private/tmp/vida-backend.log`. Keep the process running after verification.

- [ ] **Step 3: Verify backend routes**

Run:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/api/v2/dashboard
curl --fail --silent --show-error http://127.0.0.1:8000/v2/
```

Expected: dashboard JSON and an HTML document.

### Task 4: Install, start, and verify the frontend

**Files:**
- Runtime only: `frontend/node_modules/`, `/private/tmp/vida-frontend.log`

**Interfaces:**
- Consumes: `frontend/package-lock.json` and Vite config.
- Produces: Vite development server listening on `127.0.0.1:7100`, proxying `/api` to port `8000`.

- [ ] **Step 1: Install locked dependencies**

Run:

```bash
cd frontend
npm ci
```

Expected: installation succeeds from `package-lock.json`.

- [ ] **Step 2: Start Vite**

Run:

```bash
npm run dev -- --host 127.0.0.1
```

Capture logs in `/private/tmp/vida-frontend.log`. Keep the process running after verification.

- [ ] **Step 3: Verify frontend and proxy**

Run:

```bash
curl --fail --silent --show-error http://127.0.0.1:7100/v2/
curl --fail --silent --show-error http://127.0.0.1:7100/api/v2/dashboard
```

Expected: an HTML document and dashboard JSON through the Vite proxy.

### Task 5: Final verification and commit

**Files:**
- Verify: `AGENTS.md`
- Verify: `docs/devnote/2026-07-0725/vida-v2-frontend-integration.md`

**Interfaces:**
- Consumes: Tasks 1 through 4.
- Produces: committed documentation and running verified services.

- [ ] **Step 1: Run documentation checks**

Run:

```bash
git diff --check
rg -n "^## (1\\. 开发内容|2\\. 学习与可沉淀经验|3\\. 回滚操作)$" docs/devnote/2026-07-0725/vida-v2-frontend-integration.md
rg -n "docs/devnote/YYYY-MM-MMDD|8000|7100|VIDA_PROFILE_MASTER_KEY" AGENTS.md
```

Expected: no formatting errors and all mandatory content is present.

- [ ] **Step 2: Inspect repository state**

Run:

```bash
git status --short
```

Expected: only the intended documentation files are uncommitted; runtime dependencies and data remain ignored.

- [ ] **Step 3: Commit documentation**

Run:

```bash
git add AGENTS.md docs/devnote/2026-07-0725/vida-v2-frontend-integration.md docs/superpowers/plans/2026-07-25-vida-devnote-and-startup.md
git commit -m "docs: record v2 integration and startup workflow"
```

- [ ] **Step 4: Report running services**

Report the backend and frontend URLs, log paths, process identifiers, verification responses, and the exact stop commands. Do not claim Docker verification passed.

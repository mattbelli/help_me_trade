# Frontend (Next.js + shadcn)

Uses **[Bun](https://bun.sh)** for installs and scripts (faster than npm).

## Prerequisites

Install Bun (once):

```powershell
powershell -c "irm bun.sh/install.ps1 | iex"
```

Restart your terminal, then `bun --version` should work.

**Terminal opened before install?** PATH won't update until you restart. Use either:

```powershell
# Option A — helper script (no restart needed)
.\dev.ps1

# Option B — refresh PATH in this session, then use bun normally
$env:Path = "$env:USERPROFILE\.bun\bin;" + $env:Path
bun run dev
```

**Option C:** Close and reopen the terminal (or restart Cursor), then `bun run dev` works everywhere.

## Commands

```powershell
cd frontend
bun install          # install dependencies
bun run dev          # http://localhost:3001
bun run build        # production build
bun run start        # serve production build
```

Different port: `bun run dev -- -p 3005`

## Add shadcn components

```powershell
bunx shadcn@latest add card table badge
```

## API

The app reads `NEXT_PUBLIC_API_URL` from `.env.local` (default `http://127.0.0.1:8000`).
Start the Python API first: `uvicorn api.main:app --reload --host 127.0.0.1 --port 8000`

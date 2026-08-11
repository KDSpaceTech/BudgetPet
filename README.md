# BudgetPet

Mobile-first personal finance web app built with Python, SQLite and a Gemini Vision OCR API.

## Features

- Mobile-friendly 5-tab UI
- Gemini OCR for receipt/bill scanning
- Camera capture and gallery upload
- Recurring bills: add, edit, delete, mark paid
- Paid recurring bills reduce the available monthly budget
- 6 Jars budgeting
- Savings goals
- Pet selection and naming
- BudgetPet avatar upload
- User display-name editing
- Vietnamese in-app usage guide

## Requirements

- Python 3.10+
- A Gemini API key for OCR
- No third-party Python packages are required by the current source

## Local run

Windows:

```powershell
python app.py
```

Then open `http://localhost:3000`.

For a phone on the same LAN, open the computer's LAN IP and port 3000, e.g. `http://192.168.1.10:3000`.

## Environment variables

Copy `.env.example` to `.env` for local reference, but note that this app does not load `.env` automatically. Set the variables in your shell or hosting provider.

PowerShell:

```powershell
$env:GEMINI_API_KEY="YOUR_KEY"
python app.py
```

Linux/macOS:

```bash
export GEMINI_API_KEY="YOUR_KEY"
python app.py
```

Optional:

- `PORT` — HTTP port, default `3000`
- `DB_FILE` — SQLite file path; use a persistent disk/volume when deploying

## GitHub security

**Never commit a real Gemini API key.** Keep it in the hosting provider's secret/environment-variable settings. The repository intentionally contains no database file and no API key.

Because a real API key was previously pasted into chat/source during development, rotate/revoke that key before publishing this repository.

## Deployment

This repository includes:

- `Procfile` for Procfile-based Python hosts
- `Dockerfile` for container deployment
- GitHub Actions workflow that compiles `app.py` and performs a SQLite smoke test

For production, configure persistent storage for `DB_FILE`. The current app is designed as a small/personal app; for multi-user production workloads, move from SQLite to PostgreSQL.

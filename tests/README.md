# BudgetPet tests

Run the standard-library smoke tests:

```powershell
python -m unittest discover -s tests -v
```

For remote-storage adapter verification without real Turso credentials, the development fixture used during build is `test_remote.py` in the working environment. The production app itself uses Turso SQL-over-HTTP and the Platform API only when the Turso environment variables are configured.

The local smoke test explicitly clears TURSO_* environment variables, so it runs entirely against temporary local SQLite and does not require a Turso account or network access.


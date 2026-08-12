"""Minimal dependency-free Turso SQL-over-HTTP adapter.

Uses Turso's documented /v2/pipeline endpoint and the Turso Platform API.
The adapter intentionally exposes a small sqlite3-like surface so BudgetPet's
existing Python business logic remains easy to edit.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


class TursoDatabaseError(sqlite3.DatabaseError):
    pass


class TursoIntegrityError(sqlite3.IntegrityError):
    pass


class TursoOperationalError(sqlite3.OperationalError):
    pass


@dataclass
class _Result:
    cols: list[str]
    rows: list[list[Any]]
    rowcount: int = -1
    lastrowid: int | None = None


class TursoRow(dict):
    """sqlite3.Row-like mapping that also supports integer indexing."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        super().__init__(zip(columns, values))
        self._columns = list(columns)
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self):
        return self._columns


class TursoCursor:
    def __init__(self, result: _Result, connection=None):
        self._connection = connection

    @property
    def connection(self):
        """sqlite3.Cursor-compatible access to the owning connection."""
        return self._connection
        self._result = result
        self.description = [(name, None, None, None, None, None, None) for name in result.cols]
        self.rowcount = result.rowcount
        self.lastrowid = result.lastrowid
        self._index = 0

    def execute(self, sql: str, parameters=None):
        if self._connection is None:
            raise TursoDatabaseError("Cursor không gắn với connection.")
        fresh = self._connection.execute(sql, parameters)
        self._result = fresh._result
        self.description = fresh.description
        self.rowcount = fresh.rowcount
        self.lastrowid = fresh.lastrowid
        self._index = 0
        return self

    def executemany(self, sql: str, seq_of_parameters):
        if self._connection is None:
            raise TursoDatabaseError("Cursor không gắn với connection.")
        for params in seq_of_parameters:
            self.execute(sql, params)
        return self

    def fetchone(self):
        if self._index >= len(self._result.rows):
            return None
        values = self._result.rows[self._index]
        self._index += 1
        return TursoRow(self._result.cols, values)

    def fetchall(self):
        rows = self._result.rows[self._index :]
        self._index = len(self._result.rows)
        return [TursoRow(self._result.cols, values) for values in rows]

    def __iter__(self):
        return iter(self.fetchall())


class TursoConnection:
    def __init__(self, database_url: str, auth_token: str, timeout: int = 30):
        self.database_url = _normalize_database_url(database_url)
        self.auth_token = auth_token
        self.timeout = timeout
        self.closed = False

    def _post_pipeline(self, requests: list[dict]) -> list[_Result]:
        if self.closed:
            raise TursoDatabaseError("Turso connection đã đóng.")
        payload = json.dumps({"requests": requests}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.database_url.rstrip("/") + "/v2/pipeline",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise TursoOperationalError("Turso auth token không hợp lệ hoặc đã hết hạn.") from exc
            if exc.code in (429, 500, 502, 503, 504):
                raise TursoOperationalError(f"Turso HTTP {exc.code}: {detail}") from exc
            raise TursoDatabaseError(f"Turso HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TursoOperationalError(f"Không kết nối được Turso: {exc}") from exc
        except TimeoutError as exc:
            raise TursoOperationalError("Kết nối Turso timeout.") from exc

        if status != 200:
            raise TursoDatabaseError(f"Turso trả HTTP {status}.")

        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TursoDatabaseError("Phản hồi Turso không phải JSON hợp lệ.") from exc

        results: list[_Result] = []
        for item in body.get("results", []):
            if item.get("type") != "ok":
                error = item.get("error") or item.get("response") or item
                text = str(error)
                if "constraint" in text.lower() or "unique" in text.lower():
                    raise TursoIntegrityError(text)
                raise TursoDatabaseError(text)
            response = item.get("response", {})
            if response.get("type") != "execute":
                continue
            result = response.get("result", {})
            cols = [c.get("name", "") if isinstance(c, dict) else str(c) for c in result.get("cols", [])]
            rows = []
            for row in result.get("rows", []):
                decoded = [_decode_value(value) for value in row]
                rows.append(decoded)
            results.append(
                _Result(
                    cols=cols,
                    rows=rows,
                    rowcount=int(result.get("affected_row_count") or 0),
                    lastrowid=(int(result["last_insert_rowid"]) if result.get("last_insert_rowid") is not None else None),
                )
            )
        return results

    def execute(self, sql: str, parameters: Sequence[Any] | None = None):
        sql = sql.strip()
        upper = sql.upper().rstrip(";")
        if upper in {"BEGIN", "BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "COMMIT", "ROLLBACK"}:
            return TursoCursor(_Result([], [], 0, None), self)
        stmt: dict[str, Any] = {"sql": sql}
        if parameters:
            stmt["args"] = [_encode_value(v) for v in parameters]
        results = self._post_pipeline([
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ])
        if not results:
            return TursoCursor(_Result([], [], 0, None), self)
        return TursoCursor(results[0], self)

    def executescript(self, script: str):
        statements = _split_sql_script(script)
        if not statements:
            return TursoCursor(_Result([], [], 0, None), self)
        requests = []
        for stmt in statements:
            upper = stmt.strip().upper().rstrip(";")
            if upper in {"BEGIN", "BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "COMMIT", "ROLLBACK"}:
                continue
            requests.append({"type": "execute", "stmt": {"sql": stmt}})
        requests.append({"type": "close"})
        results = self._post_pipeline(requests)
        return TursoCursor(results[-1] if results else _Result([], [], 0, None))

    def cursor(self):
        return TursoCursor(_Result([], [], 0, None), self)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        self.closed = True


class TursoPlatformAPI:
    def __init__(self, org: str, token: str, timeout: int = 30, api_base: str = "https://api.turso.tech"):
        self.org = org
        self.token = token
        self.timeout = timeout
        self.base = api_base.rstrip("/") + f"/v1/organizations/{urllib.parse.quote(org, safe='')}"

    def _request(self, method: str, path: str, payload: dict | None = None):
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 409:
                raise TursoIntegrityError(detail or "Turso database đã tồn tại.") from exc
            raise TursoDatabaseError(f"Turso Platform API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TursoOperationalError(f"Không kết nối được Turso Platform API: {exc}") from exc

    def create_database(self, name: str, group: str):
        return self._request("POST", "/databases", {"name": name, "group": group})["database"]

    def get_database(self, name: str):
        return self._request("GET", f"/databases/{urllib.parse.quote(name, safe='-')}")["database"]

    def create_database_token(self, name: str, expiration: str = "30d"):
        body = self._request(
            "POST",
            f"/databases/{urllib.parse.quote(name, safe='-')}/auth/tokens?expiration={urllib.parse.quote(expiration)}&authorization=full-access",
        )
        return body["jwt"]

    def delete_database(self, name: str):
        return self._request("DELETE", f"/databases/{urllib.parse.quote(name, safe='-')}")


def connect_turso(database_url: str, auth_token: str) -> TursoConnection:
    if not database_url or not auth_token:
        raise TursoOperationalError("Thiếu TURSO_DATABASE_URL hoặc TURSO_AUTH_TOKEN.")
    return TursoConnection(database_url, auth_token)


def _normalize_database_url(url: str) -> str:
    value = url.strip()
    if value.startswith("turso://") or value.startswith("libsql://"):
        value = "https://" + value.split("://", 1)[1]
    elif not value.startswith(("http://", "https://")):
        value = "https://" + value
    value = value.rstrip("/")
    if value.endswith("/v2/pipeline"):
        value = value[:-13]
    return value


def _encode_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        # Turso's current /v2/pipeline endpoint expects REAL/float values
        # as JSON numbers. Sending the float as a JSON string (e.g.
        # "5000000.0") causes: invalid type string ..., expected f64.
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _decode_value(value: Any) -> Any:
    if not isinstance(value, dict) or "type" not in value:
        return value
    kind = value.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        try:
            return int(value.get("value", "0"))
        except (TypeError, ValueError):
            return 0
    if kind == "float":
        try:
            return float(value.get("value", "0"))
        except (TypeError, ValueError):
            return 0.0
    if kind == "blob":
        return base64.b64decode(value.get("base64", ""))
    return value.get("value", "")


def _split_sql_script(script: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for line in script.splitlines(keepends=True):
        current.append(line)
        if sqlite3.complete_statement("".join(current)):
            stmt = "".join(current).strip()
            if stmt:
                out.append(stmt)
            current = []
    tail = "".join(current).strip()
    if tail:
        out.append(tail)
    return out

"""Adapter contract and two reference implementations."""

from __future__ import annotations

import abc
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MAX_LOGICAL_TIME = (1 << 63) - 1


def _validate_logical_time(now: int) -> int:
    if type(now) is not int or now < 0 or now > MAX_LOGICAL_TIME:
        raise ValueError("logical time is outside the supported SQLite integer range")
    return now


def _bounded_time_add(now: int, delta: int, label: str) -> int:
    _validate_logical_time(now)
    if type(delta) is not int or delta < 0 or delta > MAX_LOGICAL_TIME - now:
        raise ValueError(
            "%s exceeds the supported logical-time/SQLite integer range" % label
        )
    return now + delta


def _expires_at(now: int, ttl: Optional[int]) -> Optional[int]:
    _validate_logical_time(now)
    return None if ttl is None else _bounded_time_add(now, ttl, "ttl")


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass(frozen=True)
class MemoryResult:
    memory_id: str
    text: str
    owner: str
    version: int
    score: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "owner": self.owner,
            "version": self.version,
            "score": self.score,
        }


class MemoryAdapter(abc.ABC):
    """Minimal contract used by every scenario."""

    name = "abstract"

    def __init__(self) -> None:
        self.now = 0
        self.stats: Dict[str, int] = {
            "writes": 0,
            "corrections": 0,
            "deletes": 0,
            "queries": 0,
            "records_scanned": 0,
        }

    @abc.abstractmethod
    def write(
        self,
        memory_id: str,
        owner: str,
        text: str,
        readers: Sequence[str],
        ttl: Optional[int] = None,
    ) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def correct(self, memory_id: str, actor: str, text: str, ttl: Optional[int] = None) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def delete(self, memory_id: str, actor: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def query(self, actor: str, role: str, text: str, limit: int = 5) -> List[MemoryResult]:
        raise NotImplementedError

    def advance(self, seconds: int) -> None:
        self.now = _bounded_time_add(self.now, seconds, "advance seconds")

    def fresh(self) -> "MemoryAdapter":
        """Return an empty adapter with the same configuration.

        The runner treats scenarios as independent experiments. Adapters whose
        constructors require configuration should override this method and
        carry that configuration into the returned instance.
        """
        try:
            return type(self)()
        except TypeError as exc:
            raise ValueError(
                "adapter must implement fresh() to run more than one isolated scenario"
            ) from exc

    def close(self) -> None:
        return None


class GovernedSQLiteAdapter(MemoryAdapter):
    """SQLite baseline with current-version, TTL, deletion, and ACL enforcement."""

    name = "governed-sqlite"

    def __init__(self, database: str = ":memory:") -> None:
        super().__init__()
        self.connection = sqlite3.connect(database)
        self.connection.execute(
            """
            CREATE TABLE memories (
                memory_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                text TEXT NOT NULL,
                readers TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                deleted INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    def write(
        self,
        memory_id: str,
        owner: str,
        text: str,
        readers: Sequence[str],
        ttl: Optional[int] = None,
    ) -> None:
        if not memory_id or not owner or not text:
            raise ValueError("memory_id, owner, and text are required")
        expires_at = _expires_at(self.now, ttl)
        try:
            self.connection.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, 1, ?, ?, 0)",
                (memory_id, owner, text, json.dumps(sorted(set(readers))), self.now, expires_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("memory already exists: %s" % memory_id) from exc
        except (OverflowError, sqlite3.DataError) as exc:
            raise ValueError("memory values exceed SQLite integer bounds") from exc
        self.connection.commit()
        self.stats["writes"] += 1

    def correct(self, memory_id: str, actor: str, text: str, ttl: Optional[int] = None) -> None:
        _validate_logical_time(self.now)
        row = self.connection.execute(
            "SELECT owner, version, expires_at FROM memories WHERE memory_id = ? AND deleted = 0",
            (memory_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown memory: %s" % memory_id)
        if row[0] != actor:
            raise ValueError("only the owner can correct memory: %s" % memory_id)
        expires_at = _expires_at(self.now, ttl) if ttl is not None else row[2]
        if row[1] >= MAX_LOGICAL_TIME:
            raise ValueError("memory version exceeds SQLite integer bounds")
        try:
            self.connection.execute(
                "UPDATE memories SET text = ?, version = ?, created_at = ?, expires_at = ? WHERE memory_id = ?",
                (text, row[1] + 1, self.now, expires_at, memory_id),
            )
        except (OverflowError, sqlite3.DataError) as exc:
            raise ValueError("memory values exceed SQLite integer bounds") from exc
        self.connection.commit()
        self.stats["corrections"] += 1

    def delete(self, memory_id: str, actor: str) -> None:
        row = self.connection.execute(
            "SELECT owner FROM memories WHERE memory_id = ? AND deleted = 0", (memory_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown memory: %s" % memory_id)
        if row[0] != actor:
            raise ValueError("only the owner can delete memory: %s" % memory_id)
        self.connection.execute("UPDATE memories SET deleted = 1 WHERE memory_id = ?", (memory_id,))
        self.connection.commit()
        self.stats["deletes"] += 1

    def query(self, actor: str, role: str, text: str, limit: int = 5) -> List[MemoryResult]:
        _validate_logical_time(self.now)
        rows = self.connection.execute(
            "SELECT memory_id, owner, text, readers, version, expires_at FROM memories WHERE deleted = 0 ORDER BY memory_id"
        ).fetchall()
        self.stats["queries"] += 1
        self.stats["records_scanned"] += len(rows)
        query_tokens = _tokens(text)
        matches: List[MemoryResult] = []
        for memory_id, owner, value, readers_json, version, expires_at in rows:
            if expires_at is not None and expires_at <= self.now:
                continue
            readers = set(json.loads(readers_json))
            if actor != owner and actor not in readers and role not in readers and "*" not in readers:
                continue
            score = len(query_tokens & _tokens(value))
            if score > 0 or not query_tokens:
                matches.append(MemoryResult(memory_id, value, owner, version, score))
        return sorted(matches, key=lambda item: (-item.score, item.memory_id, -item.version))[:limit]

    def close(self) -> None:
        self.connection.close()

    def fresh(self) -> MemoryAdapter:
        return GovernedSQLiteAdapter()


class LeakyAppendOnlyAdapter(MemoryAdapter):
    """Deliberately unsafe baseline used to prove that scenarios detect failures."""

    name = "leaky-append-only"

    def __init__(self) -> None:
        super().__init__()
        self.records: List[Dict[str, Any]] = []

    def write(
        self,
        memory_id: str,
        owner: str,
        text: str,
        readers: Sequence[str],
        ttl: Optional[int] = None,
    ) -> None:
        self.records.append(
            {
                "memory_id": memory_id,
                "owner": owner,
                "text": text,
                "readers": list(readers),
                "version": 1,
                "expires_at": _expires_at(self.now, ttl),
            }
        )
        self.stats["writes"] += 1

    def correct(self, memory_id: str, actor: str, text: str, ttl: Optional[int] = None) -> None:
        _validate_logical_time(self.now)
        matches = [item for item in self.records if item["memory_id"] == memory_id]
        if not matches:
            raise ValueError("unknown memory: %s" % memory_id)
        previous = matches[-1]
        self.records.append(
            {
                "memory_id": memory_id,
                "owner": previous["owner"],
                "text": text,
                "readers": list(previous["readers"]),
                "version": previous["version"] + 1,
                "expires_at": (
                    _expires_at(self.now, ttl)
                    if ttl is not None
                    else previous["expires_at"]
                ),
            }
        )
        self.stats["corrections"] += 1

    def delete(self, memory_id: str, actor: str) -> None:
        # Intentionally records the operation but retains and serves every value.
        if not any(item["memory_id"] == memory_id for item in self.records):
            raise ValueError("unknown memory: %s" % memory_id)
        self.stats["deletes"] += 1

    def query(self, actor: str, role: str, text: str, limit: int = 5) -> List[MemoryResult]:
        _validate_logical_time(self.now)
        self.stats["queries"] += 1
        self.stats["records_scanned"] += len(self.records)
        query_tokens = _tokens(text)
        matches = []
        for item in self.records:
            score = len(query_tokens & _tokens(item["text"]))
            if score > 0 or not query_tokens:
                matches.append(
                    MemoryResult(
                        item["memory_id"], item["text"], item["owner"], item["version"], score
                    )
                )
        # Old versions win ties, exposing stale-memory failures by construction.
        return sorted(matches, key=lambda item: (-item.score, item.memory_id, item.version))[:limit]

    def fresh(self) -> MemoryAdapter:
        return LeakyAppendOnlyAdapter()


def make_adapter(name: str) -> MemoryAdapter:
    if name in {"governed", "governed-sqlite"}:
        return GovernedSQLiteAdapter()
    if name in {"leaky", "leaky-append-only"}:
        return LeakyAppendOnlyAdapter()
    raise ValueError("unknown adapter: %s" % name)

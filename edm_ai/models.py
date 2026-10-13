from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Authentication itself is delegated to SSO/reverse proxy."""
    id: str
    name: str = ""
    roles: frozenset = frozenset()
    dept: str = ""
    level: int = 0  # security clearance

    @classmethod
    def from_dict(cls, d: dict) -> "Principal":
        return cls(d["id"], d.get("name", ""), frozenset(d.get("roles", [])),
                   d.get("dept", ""), int(d.get("level", 0)))


@dataclass(frozen=True)
class Acl:
    """Document ACL. Empty roles/depts/users == open to anyone meeting `level`."""
    roles: tuple = ()
    depts: tuple = ()
    users: tuple = ()
    deny_users: tuple = ()
    level: int = 0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Acl":
        d = d or {}
        return cls(tuple(d.get("roles", ())), tuple(d.get("depts", ())), tuple(d.get("users", ())),
                   tuple(d.get("deny_users", ())), int(d.get("level", 0)))

    def to_dict(self) -> dict:
        return {"roles": list(self.roles), "depts": list(self.depts), "users": list(self.users),
                "deny_users": list(self.deny_users), "level": self.level}

    def allows(self, p: Principal) -> bool:
        if p.id in self.deny_users or p.level < self.level:
            return False
        if not (self.roles or self.depts or self.users):
            return True
        return p.id in self.users or p.dept in self.depts or bool(p.roles & set(self.roles))


@dataclass
class Document:
    id: int
    source: str
    title: str
    acl: Acl


@dataclass
class Chunk:
    id: int
    doc_id: int
    ord: int
    text: str
    entities: frozenset = frozenset()  # "type:value"


@dataclass
class Evidence:
    id: str            # E1, E2 ...
    kind: str          # "doc" | "row"
    title: str
    text: str
    ref: str           # doc source#chunk, or table:key
    score: float = 0.0
    masked: tuple = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "text": self.text,
                "ref": self.ref, "score": round(self.score, 3), "masked_columns": list(self.masked)}


@dataclass
class Answer:
    text: str
    evidence: list = field(default_factory=list)
    refused: bool = False
    generator: str = "extractive"

    def to_dict(self) -> dict:
        return {"answer": self.text, "refused": self.refused, "generator": self.generator,
                "evidence": [e.to_dict() for e in self.evidence]}

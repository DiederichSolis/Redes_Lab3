from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class Message:
    proto: str               # "dijkstra" | "flooding" | "lsr" | "dvr" | ...
    type: str                # "hello" | "lsp" | "data" | "echo" | "info"
    src: str                 # nodo lógico (e.g., "A")
    dst: str                 # destino lógico (e.g., "D")
    ttl: int = 8
    headers: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        body = {
            "proto": self.proto,
            "type": self.type,
            "from": self.src,
            "to": self.dst,
            "ttl": self.ttl,
            "headers": self.headers,
            "payload": self.payload,
        }
        return json.dumps(body)

    @staticmethod
    def from_json(s: str) -> "Message":
        o = json.loads(s)
        return Message(
            proto=o.get("proto",""),
            type=o.get("type",""),
            src=o.get("from",""),
            dst=o.get("to",""),
            ttl=o.get("ttl",8),
            headers=o.get("headers",{}),
            payload=o.get("payload",{}),
        )

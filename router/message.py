# router/message.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Union

HeadersType = List[Dict[str, Any]]
PayloadType = Union[str, Dict[str, Any]]

@dataclass
class Message:
    # EXACTAMENTE los campos del PDF
    type: str                 # "message" | "echo" | "info" | "hello" | ...
    src: str                  # "from"
    dst: str                  # "to"
    hops: int = 0
    headers: HeadersType = field(default_factory=list)
    payload: PayloadType = ""

    def to_json(self) -> str:
        body = {
            "type": self.type,
            "from": self.src,
            "to": self.dst,
            "hops": int(self.hops),
            "headers": self.headers if isinstance(self.headers, list) else [],
            "payload": self.payload,
        }
        return json.dumps(body, ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "Message":
        o = json.loads(s)
        src = o.get("from", o.get("src", ""))
        dst = o.get("to",   o.get("dst", ""))

        mtype = o.get("type", "message")

        try:
            hops = int(o.get("hops", 0))
        except Exception:
            hops = 0

        headers_raw = o.get("headers", [])
        if isinstance(headers_raw, dict):
            headers: HeadersType = [headers_raw]
        elif isinstance(headers_raw, list):
            headers = [h for h in headers_raw if isinstance(h, dict)]
        else:
            headers = []

        payload_raw = o.get("payload", "")
        payload: PayloadType = payload_raw if isinstance(payload_raw, (str, dict)) else str(payload_raw)

        return Message(type=mtype, src=src, dst=dst, hops=hops, headers=headers, payload=payload)

# Constantes (opcional)
class MessageType:
    MESSAGE = "message"
    HELLO = "hello"
    ECHO = "echo"
    INFO = "info"
    LSP = "lsp"                # si lo usas internamente
    DV_ANNOUNCEMENT = "dv_announcement"

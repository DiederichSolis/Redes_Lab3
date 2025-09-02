# router/message.py
from __future__ import annotations
import json
from typing import Any, Dict, Optional

class Message:
    def __init__(
        self,
        type: str,
        src: str,
        dst: str,
        hops: int = 0,
        headers: Optional[Dict[str, Any]] = None,
        seq_num: Optional[int] = None,
        neighbors: Optional[Dict[str, float]] = None,
        payload: Any = None,
    ):
        self.type = type.lower().strip()       # hello | echo | info | message
        self.src = src                         # canal o nombre
        self.dst = dst                         # canal o nombre
        self.hops = int(hops)
        self.headers = headers or {}           # {"alg": "lsr|flooding|dvr", ...}
        self.seq_num = seq_num                 # SOLO en info (LSR)
        self.neighbors = neighbors             # SOLO en info (LSR) {nodo: costo}
        self.payload = payload                 # SOLO en message

    def to_json(self) -> str:
        d = {
            "type": self.type,
            "from": self.src,
            "to": self.dst,
            "hops": self.hops,
            "headers": self.headers,
        }
        if self.seq_num is not None:
            d["seq_num"] = self.seq_num
        if self.neighbors is not None:
            d["neighbors"] = self.neighbors
        if self.payload is not None:
            d["payload"] = self.payload
        return json.dumps(d, ensure_ascii=False)

    @staticmethod
    def from_json(raw: str) -> "Message":
        d = json.loads(raw)
        return Message(
            type=d.get("type",""),
            src=d.get("from",""),
            dst=d.get("to",""),
            hops=int(d.get("hops",0)),
            headers=d.get("headers") or {},
            seq_num=d.get("seq_num"),
            neighbors=d.get("neighbors"),
            payload=d.get("payload"),
        )

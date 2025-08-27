from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class Message:
    # Campos lógicos de tu sistema
    proto: str                     # "lsr" | "dvr" | "flooding" | "dijkstra" | "sys"
    type: str                      # "hello" | "lsp" | "data" | "echo" | "info" | "dv_announcement"
    src: str                       # origen lógico (e.g., "A")
    dst: str                       # destino lógico (e.g., "D")
    ttl: int = 8
    headers: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    # Campo requerido por la Parte 2
    hops: int = 0                  # número de saltos (incrementa en cada reenvío)

    def to_json(self) -> str:
        """
        Serializa respetando el formato base del enunciado:
          { "type", "from", "to", "hops", "headers", "payload" }
        y añade extras útiles: "proto", "ttl".
        """
        body = {
            "type": self.type,
            "from": self.src,
            "to": self.dst,
            "hops": int(self.hops),
            "headers": self.headers,
            "payload": self.payload,
            # extras (compatibilidad/diagnóstico)
            "proto": self.proto,
            "ttl": int(self.ttl),
        }
        return json.dumps(body)

    @staticmethod
    def from_json(s: str) -> "Message":
        """
        Parser tolerante: admite mensajes que vengan con "from"/"to" (enunciado)
        o "src"/"dst" (compatibilidad interna). Hace cast seguro de ttl/hops.
        """
        o = json.loads(s)

        src = o.get("from", o.get("src", ""))
        dst = o.get("to",   o.get("dst", ""))

        # Defaults razonables
        proto = o.get("proto", "lsr")
        mtype = o.get("type", "data")

        # Cast seguro
        try:
            ttl = int(o.get("ttl", 8))
        except Exception:
            ttl = 8
        try:
            hops = int(o.get("hops", 0))
        except Exception:
            hops = 0

        headers = o.get("headers", {}) or {}
        payload = o.get("payload", {}) or {}

        return Message(
            proto=proto,
            type=mtype,
            src=src,
            dst=dst,
            ttl=ttl,
            headers=headers,
            payload=payload,
            hops=hops,
        )

# Constantes para tipos de protocolo
class Protocol:
    DIJKSTRA = "dijkstra"
    DVR = "dvr"
    FLOODING = "flooding"
    LSR = "lsr"
    SYSTEM = "sys"

# Constantes para tipos de mensaje
class MessageType:
    HELLO = "hello"
    ECHO = "echo"
    LSP = "lsp"
    DATA = "data"
    INFO = "info"
    DV_ANNOUNCEMENT = "dv_announcement"

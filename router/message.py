# router/message.py
from __future__ import annotations
import json
import time
import uuid
from typing import Any, Dict, Optional


class Message:
    # Tipos
    TYPE_HELLO = "hello"
    TYPE_ECHO = "echo"
    TYPE_INFO = "info"       # LSP en LSR / INFO en general
    TYPE_MESSAGE = "message"

    # Algoritmos válidos
    VALID_ALGS = {"lsr", "flooding", "dvr", "dijkstra"}

    # Límite razonable de TTL
    MAX_HOPS = 64

    def __init__(
        self,
        type: str,
        src: str,
        dst: str,
        hops: int = 0,
        headers: Optional[Dict[str, Any]] = None,
        seq_num: Optional[int] = None,
        neighbors: Optional[Any] = None,   # list[str] o dict[str, float] (aceptamos ambos)
        payload: Any = None,
    ):
        self.type = (type or "").lower().strip()
        self.src = (src or "").strip()
        self.dst = (dst or "").strip()
        self.hops = int(hops)
        self.headers: Dict[str, Any] = headers or {}
        self.seq_num = seq_num
        self.neighbors = neighbors
        self.payload = payload

        # Asegurar id/ts en headers para dedup/RTT
        self.id = self.headers.get("id") or str(uuid.uuid4())
        self.ts = self.headers.get("ts") or time.time()
        self.headers["id"] = self.id
        self.headers["ts"] = self.ts

        self.normalize()

    # ----------------------------
    # Normalización y validación
    # ----------------------------
    def normalize(self) -> None:
        # headers dict plano
        if not isinstance(self.headers, dict):
            try:
                # si llega lista de dicts, fusiónala
                merged: Dict[str, Any] = {}
                for h in (self.headers or []):
                    if isinstance(h, dict):
                        merged.update(h)
                self.headers = merged
            except Exception:
                self.headers = {}

        # alg por defecto y normalizado
        alg = (self.headers.get("alg") or "").lower().strip()
        if alg == "dijkstra":
            alg = "lsr"
        self.headers["alg"] = alg or "lsr"
        if alg:
            self.headers["alg"] = alg
        else:
            # por defecto usamos LSR (puedes cambiarlo a flooding si lo prefieres)
            self.headers["alg"] = "lsr"
      

        # Hops acotado
        try:
            self.hops = max(0, min(int(self.hops), self.MAX_HOPS))
        except Exception:
            self.hops = 0

        # Normalización de neighbors SOLO para INFO:
        # - Si es lista: poner peso 1.0
        # - Si es dict: castear pesos a float aunque vengan como string
        if self.type == self.TYPE_INFO:
            if isinstance(self.neighbors, list):
                self.neighbors = {str(v): 1.0 for v in self.neighbors}
            elif isinstance(self.neighbors, dict):
                fixed: Dict[str, float] = {}
                for k, w in self.neighbors.items():
                    try:
                        fixed[str(k)] = float(w)
                    except Exception:
                        fixed[str(k)] = 1.0
                self.neighbors = fixed
            elif self.neighbors is None:
                self.neighbors = {}

    def validate(self) -> None:
        if self.type not in {self.TYPE_HELLO, self.TYPE_ECHO, self.TYPE_INFO, self.TYPE_MESSAGE}:
            raise ValueError(f"Unknown message type: {self.type}")
        if not self.src:
            raise ValueError("Missing src")
        # INFO puede ir dirigido a "*" (broadcast)
        if not self.dst and self.type != self.TYPE_INFO:
            raise ValueError("Missing dst")
        if self.type == self.TYPE_INFO:
            if self.headers.get("alg") not in {"lsr", "dvr", "dijkstra"}:
                # INFO suele usarse para LSR/DVR; permite dijkstra si lo usan como alias de LSR
                raise ValueError("INFO must use alg in {'lsr','dvr','dijkstra'}")
            if self.seq_num is None:
                raise ValueError("INFO requires seq_num")
            if not isinstance(self.neighbors, dict):
                raise ValueError("INFO requires neighbors as dict or list (normalized to dict)")

    # ----------------------------
    # Helpers de forwarding/ruteo
    # ----------------------------
    def alg(self) -> str:
        return (self.headers.get("alg") or "").lower().strip()

    def dec_hops(self) -> None:
        self.hops = max(0, self.hops - 1)

    def is_for_me(self, me: str) -> bool:
        # Soporta broadcast con "*"
        return self.dst == me or self.dst == "*"

    # ----------------------------
    # Serialización
    # ----------------------------
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
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
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(raw: str) -> "Message":
        try:
            d = json.loads(raw)
        except Exception as e:
            raise ValueError(f"Invalid JSON message: {e}") from e

        msg = Message(
            type=d.get("type", ""),
            src=d.get("from", ""),
            dst=d.get("to", ""),
            hops=int(d.get("hops", 0)),
            headers=d.get("headers") or {},
            seq_num=d.get("seq_num"),
            neighbors=d.get("neighbors"),
            payload=d.get("payload"),
        )

        # Asegurar id/ts por si no venían
        if "id" not in msg.headers:
            msg.headers["id"] = str(uuid.uuid4())
        if "ts" not in msg.headers:
            msg.headers["ts"] = time.time()

        # Re-normaliza por si llegó en formato alterno
        msg.normalize()
        return msg

    # ----------------------------
    # Factories de conveniencia
    # ----------------------------
    @staticmethod
    def hello(src: str, dst: str, hops: int = 8, alg: str = "flooding") -> "Message":
        return Message(
            type=Message.TYPE_HELLO, src=src, dst=dst, hops=hops,
            headers={"alg": alg}
        )

    @staticmethod
    def echo(src: str, dst: str, hops: int = 8, alg: str = "flooding") -> "Message":
        return Message(
            type=Message.TYPE_ECHO, src=src, dst=dst, hops=hops,
            headers={"alg": alg}
        )

    @staticmethod
    def info_lsr(src: str, seq_num: int, neighbors: Dict[str, float], hops: int = 16, alg: str = "lsr") -> "Message":
        # INFO para LSR/DVR (por defecto lsr). 'neighbors' ya debe ser dict con pesos.
        return Message(
            type=Message.TYPE_INFO, src=src, dst="*", hops=hops,
            headers={"alg": alg}, seq_num=seq_num, neighbors=neighbors
        )

    @staticmethod
    def data(src: str, dst: str, payload: Any, hops: int = 16, alg: str = "lsr") -> "Message":
        return Message(
            type=Message.TYPE_MESSAGE, src=src, dst=dst, hops=hops,
            headers={"alg": alg}, payload=payload
        )

    # ----------------------------
    # Debug/impresión
    # ----------------------------
    def __repr__(self) -> str:
        base = f"<Message {self.type} {self.src}->{self.dst} hops={self.hops} alg={self.alg()}>"
        if self.type == self.TYPE_INFO:
            deg = len(self.neighbors or {}) if isinstance(self.neighbors, dict) else 0
            base += f" seq={self.seq_num} deg={deg}"
        if self.type == self.TYPE_MESSAGE:
            try:
                size = len(json.dumps(self.payload)) if self.payload is not None else 0
            except Exception:
                size = 0
            base += f" payload≈{size}B"
        return base

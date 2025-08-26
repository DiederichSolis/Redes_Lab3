from __future__ import annotations

import json
import queue
import random
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from .message import Message
    from .dijkstra import dijkstra
except Exception:
    from message import Message
    from dijkstra import dijkstra

BUF = 65535


class Node:

    # ---------------- init / infra ----------------
    def __init__(
        self,
        name: str,
        bind_host: str,
        bind_port: int,
        names: Dict[str, Any],
        neighbors: List[str],
        transport: str = "udp",
        redis_cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.host = bind_host
        self.port = bind_port
        self.addr = (bind_host, bind_port)

        # Vecinos lógicos
        self.neighbors: Set[str] = set(neighbors)

        # Mapa de nombres:
        #  - UDP: logical -> {"host","port"}
        #  - Redis: logical -> "usuario"
        self.names: Dict[str, Any] = names

        # Transporte
        self.transport: str = transport.lower().strip()
        if self.transport not in ("udp", "redis"):
            raise ValueError("transport debe ser 'udp' o 'redis'")

        # Grafo dirigido con pesos: {u: {v: w, ...}, ...}
        self.graph: Dict[str, Dict[str, float]] = {}
        self.graph[self.name] = {}
        for v in self.neighbors:
            self.graph[self.name][v] = 1.0  # costo base (puedes cambiar a RTT)

        # Tabla de ruteo (next hop por destino) calculada desde self.graph
        self.routing_table: Dict[str, str] = {}

        # Control de duplicados
        self.seen_lsp_ids: Set[str] = set()
        self.seen_flood_ids: Set[str] = set()

        # RTT por vecino
        self.neighbor_rtt_ms: Dict[str, float] = {}

        # Colas / sincronización
        self.incoming: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()

        # Threads
        self.t_listener = None
        self.t_pubsub = None
        self.t_forward = None
        self.t_routing = None
        self.t_hello = None

        # UDP socket (solo si transport=udp)
        self.sock: Optional[socket.socket] = None
        if self.transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(self.addr)
            self.sock.settimeout(0.5)

        # Redis (solo si transport=redis)
        self.r = None           # redis.Redis
        self.pubsub = None      # redis.client.PubSub
        self.redis_cfg = redis_cfg or {}
        self.redis_channel_self: Optional[str] = None
        self.redis_channel_map: Dict[str, str] = {}
        self.redis_channel_prefix: Optional[str] = None
        self.redis_sec: Optional[int] = None
        self.redis_grupo: Optional[int] = None

        if self.transport == "redis":
            self._init_redis_config()

    # ---------------- Redis helpers ----------------
    def _init_redis_config(self) -> None:
        """
        Prepara construcción de canales y conexión Redis (lazily).
        """
        cfg = self.redis_cfg

        # Canales:
        # 1) channel_map explícito
        self.redis_channel_map = dict(cfg.get("channel_map", {}))

        # 2) prefix o (sec,grupo) para componer:  f"{prefix}.{usuario}"  o  f"sec{sec}.grupo{grupo}.{usuario}"
        self.redis_channel_prefix = cfg.get("channel_prefix")
        self.redis_sec = cfg.get("sec", None)
        self.redis_grupo = cfg.get("grupo", None)

        # Canal propio
        self.redis_channel_self = cfg.get("channel_self") or self._build_channel_for(self.name)

    def _ensure_redis_client(self) -> None:
        """
        Crea cliente Redis y pubsub si no existen. Importa 'redis'
        """
        if self.r is not None and self.pubsub is not None:
            return
        try:
            import redis  # type: ignore
        except Exception as e:
            raise ImportError("Falta la dependencia 'redis'. Instala con: pip install redis") from e

        host = self.redis_cfg.get("host", "localhost")
        port = int(self.redis_cfg.get("port", 6379))
        pwd = self.redis_cfg.get("pwd")

        # Conexión
        self.r = redis.Redis(host=host, port=port, password=pwd, decode_responses=True)
        # Probar conexión
        try:
            self.r.ping()
        except Exception as e:
            raise ConnectionError(f"No se pudo conectar a Redis {host}:{port}: {e}")

        # PubSub
        self.pubsub = self.r.pubsub(ignore_subscribe_messages=True)
        chan = self.redis_channel_self
        if not chan:
            raise ValueError("redis_channel_self no definido")
        self.pubsub.subscribe(chan)

    def _build_channel_for(self, logical_name: str) -> Optional[str]:

        # 1) channel_map explícito
        if logical_name in self.redis_channel_map:
            return self.redis_channel_map[logical_name]

        # 2) desde names -> usuario o canal
        user_or_channel = self.names.get(logical_name)
        if user_or_channel is None:
            return None

        # si ya parece canal completo, úsalo
        if isinstance(user_or_channel, str) and ("." in user_or_channel):
            return user_or_channel

        # construir desde usuario
        if isinstance(user_or_channel, str):
            user = user_or_channel
            if self.redis_channel_prefix:
                return f"{self.redis_channel_prefix}.{user}"
            if self.redis_sec is not None and self.redis_grupo is not None:
                return f"sec{self.redis_sec}.grupo{self.redis_grupo}.{user}"
            # sin info suficiente
            return user  # último recurso (lo tratará como canal = usuario)

        return None

    # ---------------- ciclo de vida ----------------
    def start(self) -> None:
        """Levanta hilos principales según transporte."""
        # Forwarding + Routing + Hello corren siempre
        self.t_forward = threading.Thread(target=self._forwarding_loop, daemon=True)
        self.t_routing = threading.Thread(target=self._routing_loop, daemon=True)
        self.t_hello = threading.Thread(target=self._hello_loop, daemon=True)

        # Listener según transporte
        if self.transport == "udp":
            self.t_listener = threading.Thread(target=self._listener_udp, daemon=True)
            self.t_listener.start()
        else:  # redis
            self._ensure_redis_client()
            self.t_pubsub = threading.Thread(target=self._listener_redis, daemon=True)
            self.t_pubsub.start()

        self.t_forward.start()
        self.t_routing.start()
        self.t_hello.start()

    def stop(self) -> None:
        self.stop_event.set()
        time.sleep(0.1)

        # Cierre UDP
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass

        # Cierre Redis
        if self.pubsub is not None:
            try:
                self.pubsub.close()  # type: ignore
            except Exception:
                pass
        if self.r is not None:
            try:
                self.r.close()  # type: ignore
            except Exception:
                pass

        # Join threads
        for t in (self.t_listener, self.t_pubsub, self.t_forward, self.t_routing, self.t_hello):
            try:
                if t and t.is_alive():
                    t.join(timeout=0.5)
            except Exception:
                pass

    # ---------------- envío ----------------
    def send_raw(self, logical_name: str, json_str: str) -> None:
        """
        Envía el json_str al vecino 'logical_name' usando el transporte configurado.
        """
        if self.stop_event.is_set():
            return

        if self.transport == "udp":
            self._send_raw_udp(logical_name, json_str)
        else:
            self._send_raw_redis(logical_name, json_str)

    def _send_raw_udp(self, logical_name: str, json_str: str) -> None:
        if self.sock is None:
            return
        cfg = self.names.get(logical_name)
        if not isinstance(cfg, dict) or "host" not in cfg or "port" not in cfg:
            print(f"[{self.name}] UDP send_raw: destino inválido (se esperaba host/port) -> {logical_name}")
            return
        addr = (cfg["host"], int(cfg["port"]))
        try:
            self.sock.sendto(json_str.encode("utf-8"), addr)
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] ERROR sendto({logical_name} {addr}): {e}")

    def _send_raw_redis(self, logical_name: str, json_str: str) -> None:
        try:
            self._ensure_redis_client()
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] Redis no disponible: {e}")
            return
        channel = self._build_channel_for(logical_name)
        if not channel:
            print(f"[{self.name}] Redis send_raw: no pude resolver canal para '{logical_name}'")
            return
        try:
            # Publica al canal del next-hop
            self.r.publish(channel, json_str)  # type: ignore
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] ERROR publish({channel}): {e}")

    def send(self, m: Message) -> None:
        """
        Enrutador de alto nivel.
        - Para LSR/DATA: decide next hop por routing_table; si no hay, broadcast a vecinos.
        - Para otros, si 'dst' es vecino lógico, envía directo; si no, broadcast.
        """
        if m.proto == "lsr" and m.type == "data":
            nh = self._next_hop_for(m.dst)
            if nh is None:
                for v in self.neighbors:
                    self.send_raw(v, m.to_json())
                return
            self.send_raw(nh, m.to_json())
            return

        if m.dst in self.neighbors:
            self.send_raw(m.dst, m.to_json())
        else:
            for v in self.neighbors:
                self.send_raw(v, m.to_json())

    # ---------------- listeners ----------------
    def _listener_udp(self) -> None:
        if self.sock is None:
            return
        while not self.stop_event.is_set():
            try:
                data, _addr = self.sock.recvfrom(BUF)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"[{self.name}] listener UDP error: {e}")
                continue

            try:
                raw = data.decode("utf-8")
            except Exception:
                continue
            self.incoming.put(raw)

    def _listener_redis(self) -> None:
        # Ya debe estar suscrito a channel_self
        try:
            self._ensure_redis_client()
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] listener Redis no pudo conectar: {e}")
            return

        # Loop de escucha
        while not self.stop_event.is_set():
            try:
                msg = self.pubsub.get_message(timeout=0.5)  # type: ignore
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"[{self.name}] pubsub error: {e}")
                # intenta reconectar
                time.sleep(0.5)
                try:
                    self._ensure_redis_client()
                except Exception:
                    pass
                continue

            if not msg:
                continue
            # Solo mensajes "message"
            if msg.get("type") != "message":
                continue

            data = msg.get("data")
            if not data:
                continue
            if isinstance(data, (bytes, bytearray)):
                try:
                    raw = data.decode("utf-8")
                except Exception:
                    continue
            else:
                raw = str(data)

            # Empuja a la cola para que el forwarding loop procese
            self.incoming.put(raw)

    # ---------------- forwarding ----------------
    def _forwarding_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw = self.incoming.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                m = Message.from_json(raw)
            except Exception as e:
                print(f"[{self.name}] paquete inválido: {raw[:80]}... err={e}")
                continue

            # TTL
            if m.ttl <= 0:
                continue
            m.ttl -= 1

            # --- HELLO/ECHO ---
            if m.type == "hello":
                if m.dst == self.name:
                    echo = Message(
                        proto=m.proto or "sys",
                        type="echo",
                        src=self.name,
                        dst=m.src,
                        ttl=8,
                        headers={"t0": m.headers.get("t0", 0.0)},
                        payload={},
                    )
                    self.send(echo)
                continue

            if m.type == "echo" and m.dst == self.name:
                try:
                    t0 = float(m.headers.get("t0", 0.0))
                except Exception:
                    t0 = 0.0
                rtt_ms = max(0.0, (time.time() - t0) * 1000.0)
                self.neighbor_rtt_ms[m.src] = rtt_ms
                continue

            # --- LSR: LSP reception/flood ---
            if m.proto == "lsr" and m.type == "lsp":
                lsp: Dict[str, Any] = m.payload or {}
                lsp_id = str(lsp.get("id", ""))
                node = str(lsp.get("node", ""))
                links = lsp.get("links", {}) or {}

                if not lsp_id or lsp_id in self.seen_lsp_ids:
                    continue
                self.seen_lsp_ids.add(lsp_id)

                if node not in self.graph:
                    self.graph[node] = {}
                self.graph[node].update({str(k): float(v) for k, v in links.items()})

                came_from = m.headers.get("came_from")
                for v in self.neighbors:
                    if v == came_from:
                        continue
                    fwd = Message(
                        proto="lsr",
                        type="lsp",
                        src=self.name,
                        dst=v,
                        ttl=m.ttl,
                        headers={"came_from": self.name},
                        payload=lsp,
                    )
                    self.send_raw(v, fwd.to_json())
                continue

            # --- FLOODING: DATA ---
            if m.proto == "flooding" and m.type == "data":
                msg_id = str(m.headers.get("id", ""))
                came_from = m.headers.get("came_from")
                if not msg_id:
                    continue
                if msg_id in self.seen_flood_ids:
                    continue
                self.seen_flood_ids.add(msg_id)

                if m.dst == self.name:
                    self._deliver(m)
                    continue

                for v in self.neighbors:
                    if v == came_from:
                        continue
                    fwd = Message(
                        proto="flooding",
                        type="data",
                        src=self.name,
                        dst=m.dst,
                        ttl=m.ttl,
                        headers={"id": msg_id, "came_from": self.name},
                        payload=m.payload,
                    )
                    self.send_raw(v, fwd.to_json())
                continue

            # --- DATA genérico (LSR u otros) ---
            if m.type == "data":
                if m.dst == self.name:
                    self._deliver(m)
                else:
                    self.send(m)
                continue

            # --- INFO u otros ---
            if m.type == "info" and m.dst == self.name:
                print(f"[{self.name}] INFO: {m.payload}")
                continue

    # ---------------- routing (Dijkstra + LSP emit) ----------------
    def _routing_loop(self) -> None:
        """
        1) Recalcula tabla de ruteo periódicamente.
        2) Emite un LSP con sus enlaces actuales cada ~3s.
        """
        next_lsp_at = 0.0
        while not self.stop_event.is_set():
            now = time.time()

            if self.name not in self.graph:
                self.graph[self.name] = {}
            for v in list(self.neighbors):
                self.graph.setdefault(self.name, {}).setdefault(v, 1.0)

            self._recompute_routes()

            if now >= next_lsp_at:
                self._emit_lsp()
                next_lsp_at = now + 3.0

            time.sleep(1.0)

    def _recompute_routes(self) -> None:
        # Asegura que existan nodos/entradas
        for u, nbrs in list(self.graph.items()):
            self.graph.setdefault(u, {})
            for v in list(nbrs.keys()):
                self.graph.setdefault(v, {})

        try:
            dist, prev = dijkstra(self.graph, self.name)  # type: ignore
        except Exception as e:
            print(f"[{self.name}] dijkstra error: {e}")
            return

        table: Dict[str, str] = {}
        for dest in self.graph.keys():
            if dest == self.name:
                continue
            nh = self._first_hop_from_prev(prev, dest)
            if nh:
                table[dest] = nh
        self.routing_table = table

    @staticmethod
    def _first_hop_from_prev(prev: Dict[str, Optional[str]], dest: str) -> Optional[str]:
        cur = dest
        seen = 0
        while prev.get(cur) is not None:
            p = prev[cur]
            if p is None:
                return None
            if prev.get(p) is None:
                return cur
            cur = p
            seen += 1
            if seen > 10000:
                break
        return None

    def _emit_lsp(self) -> None:
        links = {v: float(self.graph.get(self.name, {}).get(v, 1.0)) for v in self.neighbors}
        lsp = {
            "id": f"{self.name}-{int(time.time() * 1000)}-{random.randint(0, 9999)}",
            "node": self.name,
            "links": links,
        }
        for v in list(self.neighbors):
            m = Message(
                proto="lsr",
                type="lsp",
                src=self.name,
                dst=v,
                ttl=8,
                headers={"came_from": self.name},
                payload=lsp,
            )
            self.send_raw(v, m.to_json())

    # ---------------- HELLO loop ----------------
    def _hello_loop(self) -> None:
        while not self.stop_event.is_set():
            t0 = time.time()
            for v in list(self.neighbors):
                hello = Message(
                    proto="sys",
                    type="hello",
                    src=self.name,
                    dst=v,
                    ttl=4,
                    headers={"t0": t0},
                    payload={},
                )
                self.send(hello)
            time.sleep(2.0)

    # ---------------- utils de entrega/usuario ----------------
    def _deliver(self, m: Message) -> None:
        print(f"[{self.name}] DATA entregado de {m.src} → {m.dst} | payload={json.dumps(m.payload, ensure_ascii=False)}")

    def send_data(self, dst: str, text: str, ttl: int = 12) -> None:
        m = Message(proto="lsr", type="data", src=self.name, dst=dst, ttl=ttl, payload={"text": text})
        self.send(m)

    def send_data_flood(self, dst: str, text: str, ttl: int = 12) -> None:
        msg_id = f"{self.name}-{int(time.time() * 1000)}-{random.randint(0, 9999)}"
        m = Message(
            proto="flooding",
            type="data",
            src=self.name,
            dst=dst,
            ttl=ttl,
            headers={"id": msg_id, "came_from": self.name},
            payload={"text": text},
        )
        for v in list(self.neighbors):
            self.send_raw(v, m.to_json())

    # ---------------- helpers opcionales ----------------
    def _set_link_cost(self, u: str, v: str, cost: float) -> None:
        self.graph.setdefault(u, {})
        self.graph.setdefault(v, {})
        self.graph[u][v] = float(cost)

    def _next_hop_for(self, dest: str) -> Optional[str]:
        if dest == self.name:
            return self.name
        return self.routing_table.get(dest)

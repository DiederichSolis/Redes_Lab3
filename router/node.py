# router/node.py
from __future__ import annotations
import uuid

import json
import queue
import random
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# --- imports locales ---
try:
    from .message import Message
    from .dijkstra import dijkstra
except Exception:
    from message import Message
    from dijkstra import dijkstra

# --- pretty console (opcional) ---
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except Exception:
    Console = None
    Table = None
    console = None

# Emojis para logs
ICON_OK = "🟢"
ICON_NODE = "🟢"
ICON_NEUTRAL = "⚪"
ICON_SEND = "🚀"
ICON_BROADCAST = "📡"
ICON_WARN = "⚠️"
ICON_ERR = "❌"

BUF = 65535

# -------------------- Helpers de headers (lista de dicts) --------------------
HeadersType = List[Dict[str, Any]]

def hdr_get(headers: HeadersType, key: str, default=None):
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict) and key in h:
                return h[key]
    return default

def hdr_set(headers: HeadersType, key: str, value: Any):
    if not isinstance(headers, list):
        return
    for h in headers:
        if isinstance(h, dict) and key in h:
            h[key] = value
            return
    headers.append({key: value})


def _hdr_get(self, headers, key, default=None):
    """headers puede ser dict o list[dict]. Compatible con ambos."""
    if isinstance(headers, dict):
        return headers.get(key, default)
    if isinstance(headers, list):
        for d in headers:
            if isinstance(d, dict) and key in d:
                return d[key]
    return default

def _hdr_set(self, headers, key, value):
    """headers puede ser dict o list[dict]. Escribe de forma segura."""
    if isinstance(headers, dict):
        headers[key] = value
        return headers
    if isinstance(headers, list):
        # usa el primer diccionario o crea uno
        for d in headers:
            if isinstance(d, dict):
                d[key] = value
                return headers
        headers.append({key: value})
        return headers
    # si viene algo raro, conviértelo a dict
    return {key: value}

def hdr_make(**kwargs) -> HeadersType:
    return [{k: v} for k, v in kwargs.items()]



def _hdr(headers, key, default=None):
    """Lee una llave desde headers que pueden venir como dict o como list[dict]."""
    if isinstance(headers, dict):
        return headers.get(key, default)
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict) and key in h:
                return h[key]
    return default

# ---------------- Headers helpers (dict o list[dict]) ----------------
def headers_to_dict(h):
    if isinstance(h, dict):
        return dict(h)
    if isinstance(h, list):
        out = {}
        for it in h:
            if isinstance(it, dict):
                out.update(it)
        return out
    return {}

def hget(h, key, default=None):
    return headers_to_dict(h).get(key, default)

def hset(h, key, value):
    d = headers_to_dict(h)
    d[key] = value
    return d


def ensure_headers_dict(hdrs) -> dict:
    """Normaliza headers a dict (acepta None/list/dict)."""
    if isinstance(hdrs, dict):
        return dict(hdrs)
    if isinstance(hdrs, list):
        out = {}
        for it in hdrs:
            if isinstance(it, dict):
                out.update(it)
        return out
    return {}

def ttl_get(hdrs, default=8) -> int:
    try:
        return int(ensure_headers_dict(hdrs).get("ttl", default))
    except Exception:
        return default

def ttl_dec_inplace(hdrs) -> int:
    d = ensure_headers_dict(hdrs)
    t = int(d.get("ttl", 0)) - 1
    d["ttl"] = t
    return t


# -------------------- helpers de impresión --------------------
def cprint(msg: str):
    if console:
        console.print(msg)
    else:
        print(msg)

def table_nodes(title: str, rows: List[Tuple[str, str]]):
    if not console or not Table:
        for left, right in rows:
            print(f"{left} {right}")
        return
    t = Table(title=title, show_header=False, box=None, pad_edge=False)
    for left, right in rows:
        t.add_row(left, right)
    console.print(t)


# --- Canal <-> nombre lógico ---

def _dict_get(d, k, default=None):
    try:
        return d.get(k, default)
    except Exception:
        return default

def _headers_get(headers, key, default=None):
    """
    Lee headers sin importar si vienen como dict o como lista de dicts.
    """
    if isinstance(headers, dict):
        return _dict_get(headers, key, default)
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict) and key in h:
                return h[key]
    return default

def _headers_set(headers, key, value):
    """
    Escribe headers sin importar si vienen como dict o como lista de dicts.
    Si es lista, actualiza si existe o agrega al final.
    """
    if isinstance(headers, dict):
        headers[key] = value
        return
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict) and key in h:
                h[key] = value
                return
        headers.append({key: value})
        return
    # fallback
    return

def _headers_dec_ttl(headers, default=64):
    try:
        ttl = int(_headers_get(headers, "ttl", default))
    except Exception:
        ttl = default
    ttl -= 1
    _headers_set(headers, "ttl", ttl)
    return ttl


# ============================================================================

class Node:
    def __init__(
        self,
        name: str,
        bind_host: str,
        bind_port: int,
        names: Dict[str, Any],
        neighbors: List[str],
        transport: str = "udp",
        redis_cfg: Optional[Dict[str, Any]] = None,
        routing_protocol: Optional[str] = None,
    ) -> None:
        # ---- básicos
        self.name = name
        self.host = bind_host
        self.port = bind_port
        self.addr = (bind_host, bind_port)
        self.seen_flood_ids = set()

        # ---- vecinos / nombres
        self.neighbors: Set[str] = set(neighbors)
        self.names: Dict[str, Any] = names

        # ---- estado de salud/hello
        self.neighbor_rtt_ms: Dict[str, float] = {}
        self.last_seen: Dict[str, float] = {v: 0.0 for v in self.neighbors}
        self.neighbor_up: Dict[str, bool] = {v: True for v in self.neighbors}
        self.LINK_DOWN_COST = 1e9

        # ---- control de duplicated flooding
        self.seen_flood_ids: Set[str] = set()

        # ---- LSR (INFO)
        self.lsr_seq: int = 0
        self.lsr_seen_seq: Dict[str, int] = {}   # último seq por emisor lógico

        # ---- transporte
        self.transport: str = (transport or "udp").lower().strip()
        if self.transport not in ("udp", "redis"):
            raise ValueError("transport debe ser 'udp' o 'redis'")

        # ---- protocolo de ruteo para DATA
        rp = (routing_protocol or "lsr").lower().strip()
        allowed = {"lsr", "flooding", "dvr"}  # dvr opcional
        if rp not in allowed:
            rp = "lsr"
        self.routing_protocol = rp

        # ---- grafo y tabla de ruteo (LSR)
        self.graph: Dict[str, Dict[str, float]] = {self.name: {}}
        for v in self.neighbors:
            self.graph[self.name][v] = 1.0
        self.routing_table: Dict[str, str] = {}

        # ---- colas / sincronización
        self.incoming: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()

        # ---- threads
        self.t_listener = None
        self.t_pubsub = None
        self.t_forward = None
        self.t_routing = None
        self.t_hello = None
        self.t_monitor = None

        # ---- UDP socket
        self.sock: Optional[socket.socket] = None
        if self.transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(self.addr)
            self.sock.settimeout(0.5)

        # ---- Redis cliente/canales
        self.r = None
        self.pubsub = None
        self.redis_cfg = redis_cfg or {}
        self.redis_channel_self: Optional[str] = None
        self.redis_channel_map: Dict[str, str] = {}
        self._rev_channel_map: Dict[str, str] = {}

        if self.transport == "redis":
            self._init_redis_config()
        self._rev_channel_map = {}
        try:
            self._rev_channel_map = {v: k for (k, v) in (self.redis_channel_map or {}).items()}
        except Exception:
            self._rev_channel_map = {}


        # ---- identidad de canal (from)
        self.my_channel = self._channel_of(self.name)
        if getattr(self, "redis_channel_map", None):
            self._rev_channel_map = {v: k for k, v in self.redis_channel_map.items()}

    # ---------------- Redis helpers ----------------
    def _init_redis_config(self) -> None:
        if not isinstance(self.redis_cfg, dict):
            raise ValueError("[Redis] redis_cfg inválido")
        if "channel_self" not in self.redis_cfg:
            raise ValueError("[Redis] Falta 'channel_self' en redis_cfg")
        if "channel_map" not in self.redis_cfg:
            raise ValueError("[Redis] Falta 'channel_map' en redis_cfg")

        self._ensure_redis_client()

        self.redis_channel_self = str(self.redis_cfg["channel_self"])
        self.redis_channel_map = {k: str(v) for k, v in self.redis_cfg["channel_map"].items()}

        try:
            self.pubsub.subscribe(self.redis_channel_self)  # type: ignore
        except Exception as e:
            raise ValueError(f"[Redis] No se pudo suscribir a {self.redis_channel_self}: {e}")

        cprint(f"[bold green][{self.name}][/bold green] Redis listo. Canal propio = [cyan]{self.redis_channel_self}[/cyan]")

    def _ensure_redis_client(self) -> None:
        if self.r is not None and self.pubsub is not None:
            return
        try:
            import redis  # type: ignore
        except Exception as e:
            raise ImportError("Falta la dependencia 'redis'. pip install redis") from e

        host = self.redis_cfg.get("host", "lab3.redesuvg.cloud")
        port = int(self.redis_cfg.get("port", 6379))
        username = self.redis_cfg.get("username") or None
        password = self.redis_cfg.get("password") or None

        self.r = redis.Redis(
            host=host, port=port,
            username=username, password=password,
            decode_responses=True,
            health_check_interval=15,
            socket_keepalive=True,
            retry_on_timeout=True,
        )
        try:
            self.r.ping()
        except Exception as e:
            self.r = None
            self.pubsub = None
            raise ValueError(f"[Redis] No se pudo conectar/autenticar: {e}")
        self.pubsub = self.r.pubsub(ignore_subscribe_messages=True)

    def _pubsub_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.r is None or self.pubsub is None:
                    if self.stop_event.is_set():
                        break
                    self._ensure_redis_client()
                    if self.redis_channel_self:
                        try:
                            self.pubsub.subscribe(self.redis_channel_self)  # type: ignore
                        except Exception:
                            time.sleep(0.2)

                msg = self.pubsub.get_message(timeout=0.8)  # type: ignore
                if not msg:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if not data:
                    continue
                raw = data if isinstance(data, str) else str(data)
                self.incoming.put(raw)
            except Exception:
                time.sleep(0.3)
                try:
                    if self.pubsub:
                        self.pubsub.close()
                except Exception:
                    pass
                self.pubsub = None
                self.r = None

        try:
            if self.pubsub:
                self.pubsub.close()
        except Exception:
            pass
        finally:
            self.pubsub = None

    # ---------------- identidades / canales ----------------
    def _channel_of(self, logical: str) -> str:
        # Redis primero
        if getattr(self, "redis_channel_map", None):
            ch = self.redis_channel_map.get(logical)
            if ch:
                return ch
        # names.json
        cfg = self.names.get(logical)
        if isinstance(cfg, dict) and "channel" in cfg:
            return str(cfg["channel"])
        # UDP fallback host:port
        if isinstance(cfg, dict) and "host" in cfg and "port" in cfg:
            return f"{cfg['host']}:{int(cfg['port'])}"
        return logical

    def _logical_of(self, src_field: str) -> str:
        """
        Convierte 'src' (que puede venir como canal Redis o nombre lógico) a nombre lógico.
        """
        if not src_field:
            return src_field
        # Si ya es nombre lógico
        if src_field in (self.names.keys()):
            return src_field
        # Si coincide con mi canal propio
        if getattr(self, "redis_channel_self", None) and src_field == self.redis_channel_self:
            return self.name
        # Si existe en el mapa inverso
        if getattr(self, "_rev_channel_map", None):
            lg = self._rev_channel_map.get(src_field)
            if lg:
                return lg
        # names.json puede tener "channel"
        for lg, cfg in self.names.items():
            ch = cfg.get("channel")
            if ch and ch == src_field:
                return lg
        return src_field  # fallback


    # ---------------- ciclo de vida ----------------
    def start(self) -> None:
        self.t_forward = threading.Thread(target=self._forwarding_loop, daemon=True)
        self.t_routing = threading.Thread(target=self._routing_loop, daemon=True)
        self.t_hello = threading.Thread(target=self._hello_loop, daemon=True)
        self.t_monitor = threading.Thread(target=self._monitor_neighbors, daemon=True)

        if self.transport == "udp":
            self.t_listener = threading.Thread(target=self._listener_udp, daemon=True)
            self.t_listener.start()
        else:
            self._ensure_redis_client()
            if self.redis_channel_self:
                try:
                    self.pubsub.subscribe(self.redis_channel_self)  # type: ignore
                except Exception:
                    pass
            self.t_pubsub = threading.Thread(target=self._pubsub_loop, daemon=True)
            self.t_pubsub.start()

        self.t_forward.start()
        self.t_routing.start()
        self.t_hello.start()
        self.t_monitor.start()

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        for t in [self.t_forward, self.t_routing, self.t_hello, self.t_monitor, self.t_listener, self.t_pubsub]:
            if t and t.is_alive():
                try: t.join(timeout=0.8)
                except Exception: pass
        if self.transport == "udp":
            try:
                if self.sock: self.sock.close()
            except Exception: pass
            finally:
                self.sock = None
        else:
            try:
                if self.pubsub: self.pubsub.close()
            except Exception: pass
            finally:
                self.pubsub = None
            try:
                if self.r: self.r.connection_pool.disconnect()
            except Exception: pass
            finally:
                self.r = None

    # ---------------- envío / listeners ----------------
    def _sock_addr(self, logical_name: str) -> Tuple[str, int]:
        cfg = self.names.get(logical_name)
        if not isinstance(cfg, dict) or "host" not in cfg or "port" not in cfg:
            raise ValueError(f"Destino inválido (esperaba host/port) -> {logical_name}")
        return (cfg["host"], int(cfg["port"]))
    
    def send(self, m: Message) -> None:
    # Unicast con LSR/DVR
        if m.type == "message" and self.routing_protocol in {"lsr", "dvr"}:
            nh = self._next_hop_for(m.dst)
            if nh is None:
                # si no hay ruta, no inundes aquí: simplemente descarta o loguea
                for v in self.neighbors:
                    self.send_raw(v, m.to_json())
                return
            self.send_raw(nh, m.to_json())
            return

        # Control/hello/info/etc. directos al vecino si coincide, sino a todos
        if m.dst in self.neighbors:
            self.send_raw(m.dst, m.to_json())
        else:
            for v in self.neighbors:
                self.send_raw(v, m.to_json())

    def send_raw(self, next_hop: str, raw_json: str) -> None:
        if self.stop_event.is_set():
            return
        if not isinstance(raw_json, str):
            try: raw_json = json.dumps(raw_json)
            except Exception: raw_json = str(raw_json)

        if self.transport == "redis":
            ch = (self.redis_channel_map or {}).get(next_hop)
            if not ch:
                cprint(f"[{self.name}] Canal Redis desconocido para '{next_hop}'")
                return
            try:
                self._ensure_redis_client()
                self.r.publish(ch, raw_json)  # type: ignore
            except Exception as e:
                cprint(f"{ICON_ERR} [red][{self.name}][/red] publish Redis → [magenta]{ch}[/magenta]: {e}")
            return

        try:
            addr = self._sock_addr(next_hop)
        except Exception as e:
            cprint(f"[{self.name}] No pude resolver addr de '{next_hop}': {e}")
            return
        try:
            if self.sock is not None:
                self.sock.sendto(raw_json.encode("utf-8"), addr)
        except Exception as e:
            cprint(f"[{self.name}] Error UDP sendto -> {next_hop}: {e}")

    def _listener_udp(self) -> None:
        if self.sock is None:
            return
        while not self.stop_event.is_set():
            try:
                data, _addr = self.sock.recvfrom(BUF)
            except socket.timeout:
                continue
            except Exception as e:
                cprint(f"[{self.name}] listener UDP error: {e}")
                continue
            try:
                raw = data.decode("utf-8")
            except Exception:
                continue
            self.incoming.put(raw)

    # ---------------- forwarding ----------------
    def _forwarding_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw = self.incoming.get(timeout=0.2)
            except queue.Empty:
                continue

            # Parseo seguro
            try:
                m = Message.from_json(raw)
            except Exception as e:
                cprint(f"{ICON_ERR} [red][{self.name}][/red] paquete inválido: {raw[:120]}… err={e}")
                continue

            # Headers como dict (nunca list[dict])
            m.headers = headers_to_dict(getattr(m, "headers", {}))

            # Normaliza nombres lógicos
            src_log = self._logical_of(m.src)
            dst_log = self._logical_of(m.dst)

            # TTL común (default 64)
            try:
                ttl = int(hget(m.headers, "ttl", 64))
            except Exception:
                ttl = 64
            if ttl <= 0:
                continue
            ttl -= 1
            m.headers = hset(m.headers, "ttl", ttl)

            # ===== HELLO / ECHO =====
            if m.type == "hello":
                self.last_seen[src_log] = time.time()
                self._link_up(src_log)
                if dst_log == self.name:
                    t0 = float(hget(m.headers, "t0", 0.0))
                    echo = Message(type="echo", src=self.name, dst=src_log, hops=0, headers={"t0": t0})
                    self.send(echo)
                continue

            if m.type == "echo" and dst_log == self.name:
                t0 = float(hget(m.headers, "t0", 0.0))
                self.neighbor_rtt_ms[src_log] = max(0.0, (time.time() - t0) * 1000.0)
                continue

            # ===== INFO (LSR) =====
            if m.type == "info" and hget(m.headers, "alg") == "lsr":
                seq = int(getattr(m, "seq_num", 0))
                last = self.lsr_seen_seq.get(src_log, -1)
                if seq > last:
                    self.lsr_seen_seq[src_log] = seq
                    links = getattr(m, "neighbors", {}) or {}
                    self.graph.setdefault(src_log, {})
                    self.graph[src_log].update({str(k): float(v) for k, v in links.items()})
                    if self.routing_protocol == "lsr":
                        self._recompute_routes()
                    # re-difunde a otros (excepto quien lo trajo)
                    for v in list(self.neighbors):
                        if v == src_log:
                            continue
                        fwd = Message(type="info", src=self.name, dst=v, hops=m.hops + 1,
                                    headers={"alg": "lsr"}, seq_num=seq, neighbors=links)
                        self.send_raw(v, fwd.to_json())
                continue

            # ===== DATA =====
            if m.type == "message":
                proto = (self.routing_protocol or "").lower()

                # --- FLOODING ---
                if proto == "flooding":
                    # de-duplicado por id
                    msg_id = hget(m.headers, "id")
                    if not msg_id:
                        msg_id = f"gen-{uuid.uuid4()}"
                        m.headers = hset(m.headers, "id", msg_id)
                    if msg_id in self.seen_flood_ids:
                        continue
                    self.seen_flood_ids.add(msg_id)

                    # ¿soy destino?
                    if dst_log == self.name:
                        self._deliver(m)
                        continue

                    # TTL agotado = no reenvía
                    if ttl <= 0:
                        continue

                    # reenvía a todos mis vecinos excepto el emisor lógico
                    m.hops = int(getattr(m, "hops", 0)) + 1
                    m.headers = hset(m.headers, "via", self.name)
                    for v in list(self.neighbors):
                        if v == src_log:
                            continue
                        self.send_raw(v, m.to_json())
                    continue

                # --- LSR/DVR (unicast por next-hop, sin inundar si no hay ruta) ---
                nh = self.routing_table.get(dst_log)
                if nh and ttl > 0:
                    m.hops = int(getattr(m, "hops", 0)) + 1
                    self.send_raw(nh, m.to_json())
                continue


    # ---------------- routing (LSR) ----------------
    def _recompute_routes(self) -> None:
        # completa nodos faltantes
        for u, nbrs in list(self.graph.items()):
            self.graph.setdefault(u, {})
            for v in list(nbrs.keys()):
                self.graph.setdefault(v, {})

        try:
            dist, prev = dijkstra(self.graph, self.name)  # type: ignore
        except Exception as e:
            cprint(f"[{self.name}] dijkstra error: {e}")
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

    # INFO (LSR) periódico o por cambio de enlace
        # --- en Node (público) ---
    def emit_info(self) -> None:
        """
        Emite un INFO (LSP) real a cada vecino directo con el formato del protocolo:
        {
        "type": "info",
        "from": <canal o nombre>,
        "to": <vecino>,
        "hops": 0,
        "headers": {"alg": "lsr"},
        "seq_num": <entero creciente por emisor>,
        "neighbors": {"V1": costo, "V2": costo, ...}
        }
        """
        # ++ secuencia local
        self.lsr_seq += 1
        # costo actual a cada vecino
        links = {v: float(self.graph.get(self.name, {}).get(v, 1.0)) for v in self.neighbors}

        for v in list(self.neighbors):
            m = Message(
                type="info",
                src=self.name,          # o self.my_channel si tu Message espera canal
                dst=v,                  # vecino lógico (Node.send_raw resuelve canal)
                hops=0,
                headers={"alg": "lsr"},
                seq_num=self.lsr_seq,
                neighbors=links,
            )
            self.send_raw(v, m.to_json())
        
    def _emit_info_lsr(self, only_to: str | None = None) -> None:
        """
        Envía un paquete INFO (LSR) con:
        - type = "info"
        - headers = {"alg": "lsr"}
        - seq_num = contador local creciente
        - neighbors = { vecino: costo }
        Se envía a todos los vecinos, o sólo a 'only_to' si se pasa.
        """
        self.lsr_seq = int(getattr(self, "lsr_seq", 0)) + 1
        # Tomamos costos actuales hacia cada vecino
        links = {v: float(self.graph.get(self.name, {}).get(v, 1.0)) for v in list(self.neighbors)}

        destinos = [only_to] if only_to else list(self.neighbors)
        for v in destinos:
            try:
                m = Message(
                    type="info",
                    src=self.name,        # lógico propio (A, B, etc.)
                    dst=v,                # vecino lógico
                    hops=0,
                    headers={"alg": "lsr"},
                    seq_num=self.lsr_seq,
                    neighbors=links,
                )
                self.send_raw(v, m.to_json())
            except Exception as e:
                print(f"[{self.name}] error enviando INFO LSR a {v}: {e}")


    # ---------------- HELLO / monitor ----------------
    def _hello_loop(self) -> None:
        TIMEOUT = 6.0
        while not self.stop_event.is_set():
            # envía HELLO a todos
            for v in list(self.neighbors):
                self.send_hello(v, ping=False)
            # marca down por timeout (también lo hace monitor, pero aquí es extra)
            now = time.time()
            for v in list(self.neighbors):
                last = self.last_seen.get(v, 0.0)
                if (now - last) > TIMEOUT and self.neighbor_up.get(v, True):
                    self._link_down(v)
            time.sleep(2.0)

    def _monitor_neighbors(self) -> None:
        TIMEOUT = 8.0
        while not self.stop_event.is_set():
            now = time.time()
            for v in list(self.neighbors):
                last = self.last_seen.get(v, 0.0)
                if (now - last) > TIMEOUT:
                    # poner costo ∞ si no lo estaba
                    if self.graph.get(self.name, {}).get(v, 1.0) < self.LINK_DOWN_COST:
                        self.graph[self.name][v] = self.LINK_DOWN_COST
                        cprint(f"{ICON_WARN} [{self.name}] Vecino {v} INACTIVO → costo ∞")
                        if self.routing_protocol == "lsr":
                            self._emit_info_lsr()
                else:
                    # restaurar si estaba en ∞
                    if self.graph.get(self.name, {}).get(v, self.LINK_DOWN_COST) >= self.LINK_DOWN_COST:
                        self.graph[self.name][v] = 1.0
                        cprint(f"{ICON_OK} [{self.name}] Vecino {v} restaurado → costo 1.0")
                        if self.routing_protocol == "lsr":
                            self._emit_info_lsr()
            time.sleep(2.0)

    def _link_down(self, v: str):
        if self.neighbor_up.get(v, True):
            self.neighbor_up[v] = False
            self.graph.setdefault(self.name, {})[v] = self.LINK_DOWN_COST
            cprint(f"[{self.name}] ⚠️ Vecino {v} marcado DOWN")
            if self.routing_protocol == "lsr":
                self._emit_info_lsr()

    def _link_up(self, v: str):
        if not self.neighbor_up.get(v, False):
            self.neighbor_up[v] = True
            self.graph.setdefault(self.name, {})[v] = 1.0
            cprint(f"[{self.name}] 🟢 Vecino {v} de vuelta (UP)")
            if self.routing_protocol == "lsr":
                self._emit_info_lsr()

    # ---------------- utils usuario ----------------
    def _deliver(self, m: Message) -> None:
        try:
            payload_str = json.dumps(m.payload, ensure_ascii=False)
        except Exception:
            payload_str = str(m.payload)
        cprint(
            f"[bold green][{self.name}][/bold green] MESSAGE entregado "
            f"de [magenta]{self._logical_of(m.src)}[/magenta] → [cyan]{self._logical_of(m.dst)}[/cyan] "
            f"(hops={getattr(m,'hops','?')}) | payload={payload_str}"
        )

    def print_nodes(self):
        rows = []
        for k, cfg in self.names.items():
            icon = ICON_NODE if k == self.name else (ICON_OK if self.neighbor_up.get(k, False) else ICON_NEUTRAL)
            canal = cfg.get("channel", cfg.get("chan", ""))
            left = f"{icon} {('[bold](YO)[/bold] ' if k==self.name else '')}{k}"
            right = f"→ {canal}"
            rows.append((left, right))
        table_nodes("NODOS DISPONIBLES", rows)

    # ---------------- high-level send ----------------
    def _next_hop_for(self, dest: str) -> Optional[str]:
        if dest == self.name:
            return self.name
        return self.routing_table.get(dest)

    def send(self, m: Message) -> None:
        """
        Unifica envío:
        - message con alg=lsr/dvr: usa routing_table (unicast); si no hay ruta → broadcast a vecinos
        - hello/echo/info: si dst es vecino, directo; si no, broadcast
        - flooding: siempre por vecinos
        """
        alg = hdr_get(m.headers, "alg", None)

        # Unicast de data (lsr/dvr)
        if m.type == "message" and alg in ("lsr", "dvr", "dijkstra"):
            dest_log = self._logical_of(m.dst)
            nh = self._next_hop_for(dest_log)
            if nh is None:
                for v in self.neighbors:
                    self.send_raw(v, m.to_json())
                return
            self.send_raw(nh, m.to_json())
            return

        # flooding
        if m.type == "message" and alg == "flooding":
            for v in self.neighbors:
                self.send_raw(v, m.to_json())
            return

        # control (hello/echo/info)
        dst_log = self._logical_of(m.dst)
        if dst_log in self.neighbors:
            self.send_raw(dst_log, m.to_json())
        else:
            for v in self.neighbors:
                self.send_raw(v, m.to_json())

    # ---------------- APIs para demo/CLI ----------------

    def send_hello(self, dst: str, ping: bool = False):
        m = Message(
            type="hello",
            src=self.name,
            dst=dst,
            hops=0,
            headers={"ttl": 3, "t0": time.time(), "alg": self.routing_protocol},
            payload=""
        )
        self.send(m)



    def send_data_flood(self, dst: str, text: str, ttl: int = 8) -> bool:
        """Inicia un flooding unicast con ID único y TTL."""
        msg_id = str(uuid.uuid4())
        headers = {"ttl": int(ttl), "id": msg_id, "via": self.name}

        m = Message(
            type="message",
            src=self.name,   # lógico
            dst=dst,         # lógico (A, B, C…)
            hops=0,
            headers=headers,
            payload={"text": text},
        )

        # Márcalo como visto para que si rebota a mí, lo ignore
        self.seen_flood_ids.add(msg_id)

        # Envía a TODOS mis vecinos (un solo “arranque”)
        for v in list(self.neighbors):
            self.send_raw(v, m.to_json())
        return True






    def send_data(self, dst: str, text: str, ttl: int = 8):
        # Si el protocolo del nodo es flooding, usa flooding.
        if getattr(self, "routing_protocol", "").lower() == "flooding":
            return self.send_data_flood(dst, text, ttl=ttl)

        # --- LSR/DVR (lo que ya tenías). Ejemplo:
        nh = self.routing_table.get(dst)
        if not nh:
            # No hay ruta: no intentes usar forwarding hop-by-hop si no hay next-hop
            # para LSR/DVR; evita el "no manda el send".
            self._log(f"⚠️ sin ruta a {dst} (protocolo {self.routing_protocol})")
            return False

        from router.message import Message
        m = Message(
            type="message",
            src=self.name,
            dst=dst,
            hops=0,
            headers={"ttl": int(ttl)},
            payload={"text": text}
        )
        self._send_to_logical(nh, m)
        return True



   

    # ---------------- loop de ruteo ----------------
    def _routing_loop(self) -> None:
        next_info_at = 0.0
        while not self.stop_event.is_set():
            now = time.time()

            # asegura base
            if self.name not in self.graph:
                self.graph[self.name] = {}
            for v in list(self.neighbors):
                self.graph.setdefault(self.name, {}).setdefault(v, 1.0)

            if self.routing_protocol == "lsr":
                self._recompute_routes()
                if now >= next_info_at:
                    self._emit_info_lsr()
                    next_info_at = now + 3.0

            # (si implementas DVR, este es el lugar de _emit_dv / _dvr_recompute)

            time.sleep(1.0)

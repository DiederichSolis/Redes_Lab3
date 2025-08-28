from __future__ import annotations

import json
import queue
import random
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Set

try:
    from .message import Message
    from .dijkstra import dijkstra
except Exception:
    from message import Message
    from dijkstra import dijkstra

# --- pretty console ---
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except Exception:
    Console = None
    Table = None
    console = None

# emojis e íconos
ICON_OK = "🟢"
ICON_NODE = "🟢"
ICON_NEUTRAL = "⚪"
ICON_SEND = "🚀"
ICON_BROADCAST = "📡"
ICON_WARN = "⚠️"
ICON_ERR = "❌"

def cprint(msg: str):
    if console:
        console.print(msg)
    else:
        print(msg)

def table_nodes(title: str, rows: list[tuple[str,str]]):
    if not console or not Table:
        for left, right in rows:
            print(f"{left} {right}")
        return
    t = Table(title=title, show_header=False, box=None, pad_edge=False)
    for left, right in rows:
        t.add_row(left, right)
    console.print(t)


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
        routing_protocol: Optional[str] = None,
    ) -> None:
        self.name = name
        self.host = bind_host
        self.port = bind_port
        self.addr = (bind_host, bind_port)

        # Vecinos lógicos
        self.neighbors: Set[str] = set(neighbors)

        # Mapa de nombres
        self.names: Dict[str, Any] = names

        # Transporte
        self.transport: str = transport.lower().strip()
        if self.transport not in ("udp", "redis"):
            raise ValueError("transport debe ser 'udp' o 'redis'")

        # ---------------- routing ----------------
        # normaliza y valida el protocolo: lsr | dvr | flooding
        rp = (routing_protocol or "lsr").lower().strip()
        allowed = {"lsr", "dvr", "flooding"}
        if rp not in allowed:
            raise ValueError(
                f"routing_protocol inválido '{rp}'. Usa uno de {sorted(allowed)}"
            )
        self.routing_protocol: str = rp

        # Grafo dirigido con pesos: {u: {v: w, ...}}
        self.graph: Dict[str, Dict[str, float]] = {}
        self.graph[self.name] = {}
        for v in self.neighbors:
            self.graph[self.name][v] = 1.0  # costo base

        # Tabla de ruteo (next hop por destino)
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

        # UDP socket (sólo si transport=udp)
        self.sock: Optional[socket.socket] = None
        if self.transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(self.addr)
            self.sock.settimeout(0.5)

        # Redis (sólo si transport=redis)
        self.r = None            # redis.Redis
        self.pubsub = None       # redis.client.PubSub
        self.redis_cfg = redis_cfg or {}
        self.redis_channel_self: Optional[str] = None
        self.redis_channel_map: Dict[str, str] = {}

        if self.transport == "redis":
            self._init_redis_config()

    # ---------------- Redis helpers ----------------
    def _init_redis_config(self) -> None:
        """
        Inicializa configuración Redis y valida llaves requeridas.
        No inicia hilos aquí; sólo prepara cliente y suscripción.
        """
        if not isinstance(self.redis_cfg, dict):
            raise ValueError("[Redis] redis_cfg inválido")
        if "channel_self" not in self.redis_cfg:
            raise ValueError("[Redis] Falta 'channel_self' en redis_cfg")
        if "channel_map" not in self.redis_cfg:
            raise ValueError("[Redis] Falta 'channel_map' en redis_cfg")

        # Crea cliente y pubsub con auth
        self._ensure_redis_client()

        # Canales
        self.redis_channel_self = str(self.redis_cfg["channel_self"])
        self.redis_channel_map = {k: str(v) for k, v in self.redis_cfg["channel_map"].items()}

        # Suscripción a canal propio (pubsub ya existe tras _ensure_redis_client)
        try:
            self.pubsub.subscribe(self.redis_channel_self)  # type: ignore
        except Exception as e:
            raise ValueError(f"[Redis] No se pudo suscribir a {self.redis_channel_self}: {e}")

        cprint(f"[bold green][{self.name}][/bold green] Redis listo. Canal propio = [cyan]{self.redis_channel_self}[/cyan]")


    def _ensure_redis_client(self) -> None:
        """
        Crea cliente Redis y pubsub si no existen. Importa 'redis' perezosamente.
        Unifica auth con username/password.
        """
        if self.r is not None and self.pubsub is not None:
            return

        try:
            import redis  # type: ignore
        except Exception as e:
            raise ImportError("Falta la dependencia 'redis'. Instálala con: pip install redis") from e

        host = self.redis_cfg.get("host", "lab3.redesuvg.cloud")
        port = int(self.redis_cfg.get("port", 6379))
        username = self.redis_cfg.get("username") or None
        password = self.redis_cfg.get("password") or None

        self.r = redis.Redis(
            host=host,
            port=port,
            username=username,
            password=password,
            decode_responses=True,
            health_check_interval=15,
            socket_keepalive=True,
            retry_on_timeout=True,
        )

        # Verificar conexión / autenticación
        try:
            self.r.ping()
        except Exception as e:
            # Reset si falló
            self.r = None
            self.pubsub = None
            raise ValueError(f"[Redis] No se pudo conectar/autenticar: {e}")

        # Pub/Sub base (suscripción al canal propio se hace en _init_redis_config)
        self.pubsub = self.r.pubsub(ignore_subscribe_messages=True)

    def _pubsub_loop(self) -> None:
        """
        Listener de Pub/Sub robusto:
        - usa get_message(timeout) para no bloquear
        - reintenta suscripción si cae la conexión
        - no loguea ni reintenta si estamos en shutdown
        """
        while not self.stop_event.is_set():
            try:
                # Asegurar cliente y pubsub vivos
                if self.r is None or self.pubsub is None:
                    if self.stop_event.is_set():
                        break
                    self._ensure_redis_client()
                    # Re-suscribir a canal propio si es necesario
                    if self.redis_channel_self:
                        try:
                            self.pubsub.subscribe(self.redis_channel_self)  # type: ignore
                        except Exception:
                            # se reintenta en el próximo ciclo
                            time.sleep(0.2)

                # Lectura no bloqueante
                msg = self.pubsub.get_message(timeout=0.8)  # type: ignore
                if not msg:
                    continue
                if msg.get("type") != "message":
                    continue

                data = msg.get("data")
                if not data:
                    continue

                if isinstance(data, bytes):
                    try:
                        raw = data.decode("utf-8", "ignore")
                    except Exception:
                        continue
                else:
                    raw = str(data)

                # Encolar para el forwarding
                self.incoming.put(raw)

            except (AttributeError, OSError, ValueError) as e:
                if self.stop_event.is_set():
                    break
                # Recrear cliente/pubsub
                try:
                    if self.pubsub:
                        self.pubsub.close()
                except Exception:
                    pass
                self.pubsub = None
                self.r = None
                time.sleep(0.3)

            except Exception:
                if self.stop_event.is_set():
                    break
                # Reset y reintento
                try:
                    if self.pubsub:
                        self.pubsub.close()
                except Exception:
                    pass
                self.pubsub = None
                self.r = None
                time.sleep(0.3)

        # salida limpia
        try:
            if self.pubsub:
                self.pubsub.close()
        except Exception:
            pass
        finally:
            self.pubsub = None

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
            # Asegura cliente y suscripción, luego un único loop de pubsub
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

    def stop(self) -> None:
        """Apagado limpio: señal, join de hilos y cierre de transports."""
        # Evita doble stop
        if self.stop_event.is_set():
            return

        # 1) Señal de parada
        self.stop_event.set()

        # 2) Unir hilos primero (así no usan recursos ya cerrados)
        for t in [self.t_forward, self.t_routing, self.t_hello, self.t_listener, self.t_pubsub]:
            if t and t.is_alive():
                try:
                    t.join(timeout=0.8)
                except Exception:
                    pass

        # 3) Cerrar transports/sockets después de unir hilos
        if self.transport == "udp":
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            finally:
                self.sock = None
        elif self.transport == "redis":
            try:
                if self.pubsub:
                    self.pubsub.close()
            except Exception:
                pass
            finally:
                self.pubsub = None
            try:
                if self.r:
                    self.r.connection_pool.disconnect()
            except Exception:
                pass
            finally:
                self.r = None

        # 4) Limpieza de refs a hilos
        self.t_forward = None
        self.t_routing = None
        self.t_hello = None
        self.t_listener = None
        self.t_pubsub = None

    # ---------------- envío ----------------
    def send_raw(self, next_hop: str, raw_json: str) -> None:
        """
        next_hop es el nombre lógico del vecino.
        - Redis: publish al canal del vecino
        - UDP: sendto al (host, port) correspondiente
        Silencia errores cuando estamos en shutdown.
        """
        if self.stop_event.is_set():
            return

        # Asegura string
        if not isinstance(raw_json, str):
            try:
                raw_json = json.dumps(raw_json)
            except Exception:
                raw_json = str(raw_json)

        if self.transport == "redis":
            ch = (self.redis_channel_map or {}).get(next_hop)
            if not ch:
                if not self.stop_event.is_set():
                    print(f"[{self.name}] Canal Redis desconocido para '{next_hop}'")
                return
            try:
                self._ensure_redis_client()  # robustez ante reconexión
                self.r.publish(ch, raw_json)  # type: ignore
            except Exception as e:
                if not self.stop_event.is_set():
                    cprint(f"{ICON_ERR} [red][{self.name}][/red] publish Redis → [magenta]{ch}[/magenta]: {e}")

            return

        # --- UDP ---
        try:
            addr = self._sock_addr(next_hop)
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] No pude resolver addr de '{next_hop}': {e}")
            return

        try:
            if self.sock is not None:
                self.sock.sendto(raw_json.encode("utf-8"), addr)
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[{self.name}] Error UDP sendto -> {next_hop}: {e}")

    def _sock_addr(self, logical_name: str) -> tuple[str, int]:
        cfg = self.names.get(logical_name)
        if not isinstance(cfg, dict) or "host" not in cfg or "port" not in cfg:
            raise ValueError(f"Destino inválido (se esperaba host/port) -> {logical_name}")
        return (cfg["host"], int(cfg["port"]))

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

    # ---------------- forwarding ----------------
    def _forwarding_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw = self.incoming.get(timeout=0.2)
            except queue.Empty:
                continue

            # Parseo robusto
            try:
                m = Message.from_json(raw)
            except Exception as e:
                cprint(f"{ICON_ERR} [red][{self.name}][/red] paquete inválido: {raw[:80]}… err={e}")
                continue

            # ---------- Normalización y actualización de TTL/HOPS ----------
            # HOPS: +1 por cada salto (forwarding)
            try:
                m.hops = int(getattr(m, "hops", 0)) + 1
            except Exception:
                m.hops = 1

            # TTL: si llega agotado, descarta; si no, decrementa
            try:
                m.ttl = int(getattr(m, "ttl", 8))
            except Exception:
                m.ttl = 8
            if m.ttl <= 0:
                continue
            m.ttl -= 1
            # ---------------------------------------------------------------

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
                        hops=0,  # nuevo mensaje de respuesta
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
                        hops=m.hops,  # propaga hops actualizados
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
                        hops=m.hops,  # propaga hops
                    )
                    self.send_raw(v, fwd.to_json())
                continue

            # --- DATA genérico (LSR/DVR) ---
            if m.type == "data":
                if m.dst == self.name:
                    self._deliver(m)  # entrega final (usa m.hops actualizado)
                else:
                    # Reenvío: 'send' decide next-hop y usa m.to_json() internamente
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
        2) Emite LSP con enlaces actuales cada ~3s.
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
        # Asegurar nodos/entradas
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
        # imprime hops y payload bonito
        try:
            payload_str = json.dumps(m.payload)
        except Exception:
            payload_str = str(m.payload)
        cprint(
        f"[bold green][{self.name}][/bold green] DATA entregado "
        f"de [magenta]{m.src}[/magenta] → [cyan]{m.dst}[/cyan] "
        f"(hops={getattr(m,'hops','?')}) | payload={payload_str}"
        )


    def send(self, m: Message) -> None:
        """
        Enrutador de alto nivel.
        - Para LSR/DATA: decide next hop por routing_table; si no hay, broadcast.
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

    def send_hello(self, dst: str, ttl: int = 8) -> None:
        m = Message(
            proto="sys",
            type="hello",
            src=self.name,
            dst=dst,
            ttl=ttl,
            headers={"t0": time.time()},
            payload={},
            hops=0,
        )
        self.send(m)

    def send_data(self, dst: str, text: str, ttl: int = 12) -> None:
        if self.routing_protocol == "flooding":
            self.send_data_flood(dst, text, ttl=ttl)
            return
        m = Message(
            proto=self.routing_protocol,  # "lsr" o "dvr"
            type="data",
            src=self.name,
            dst=dst,
            ttl=ttl,
            headers={},
            payload={"text": text},
            hops=0,                       # ← arranca en 0
        )
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
            # 👇 El log debe ir DENTRO del bucle, ya con v definido
            try:
                ch = (self.redis_channel_map or {}).get(v)
            except Exception:
                ch = None
            if 'cprint' in globals():
                cprint(f"[{self.name}] {ICON_SEND} enviando inicial a [green]{v}[/green]{' ('+str(ch)+')' if ch else ''}")
            self.send_raw(v, m.to_json())


    # ---------------- helpers opcionales ----------------
    def _set_link_cost(self, u: str, v: str, cost: float) -> None:
        self.graph.setdefault(u, {})
        self.graph.setdefault(v, {})
        self.graph[u][v] = float(cost)

    def print_nodes(self):
        rows = []
        for k, cfg in self.names.items():
            icon = ICON_NODE if k == self.name else ICON_NEUTRAL
            canal = cfg.get("channel", cfg.get("chan", ""))
            left = f"{icon} {('[bold](YO)[/bold] ' if k==self.name else '')}{k}"
            right = f"→ {canal}"
            rows.append((left, right))
        table_nodes("NODOS DISPONIBLES", rows)


    def _next_hop_for(self, dest: str) -> Optional[str]:
        if dest == self.name:
            return self.name
        return self.routing_table.get(dest)

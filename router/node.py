# router/node.py
from __future__ import annotations
import time
import threading
import queue
from typing import Dict, List, Set, Optional, Tuple, Any

from .message import Message
from .dvr import DistanceVectorRouter  # Asegúrate de que dvr.py esté en el PYTHONPATH

# Mini paleta de colores para logs del nodo
class CC:
    RESET="\033[0m"; BOLD="\033[1m"
    G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"; M="\033[95m"; B="\033[94m"; GR="\033[90m"


# --- Dijkstra: grafo ponderado no dirigido ---
def dijkstra(graph: Dict[str, Dict[str, float]], src: str):
    """Retorna (dist, prev). dist[n] = costo mínimo desde src; prev[n] = padre en la ruta óptima."""
    nodes: Set[str] = set(graph.keys())
    for u, nbrs in graph.items():
        nodes.add(u)
        nodes.update(nbrs.keys())

    dist: Dict[str, float] = {u: float("inf") for u in nodes}
    prev: Dict[str, Optional[str]] = {u: None for u in nodes}
    if src not in nodes:
        return dist, prev
    dist[src] = 0.0

    visited: Set[str] = set()
    while True:
        u = None
        best = float("inf")
        for n, d in dist.items():
            if n not in visited and d < best:
                best = d
                u = n
        if u is None:
            break
        visited.add(u)
        for v, w in graph.get(u, {}).items():
            if v in visited:
                continue
            alt = dist[u] + float(w)
            if alt < dist.get(v, float("inf")):
                dist[v] = alt
                prev[v] = u
    return dist, prev


class Node:
    """
    Nodo de ruteo con:
      - Descubrimiento: HELLO/ECHO (ping/RTT)
      - LSR: INFO (LSP con seq_num + neighbors{n:cost}) + Dijkstra
      - DVR: INFO (vector de distancias {dest:dist})
      - Mensajes de usuario: MESSAGE (alg=lsr|flooding|dvr)
      - Broadcast: MESSAGE con dst="*"
      - Robustez: reconexión Redis, detección vecino up/down y anuncios por cambio
    Transporte: Redis Pub/Sub (canal por nodo desde names-redis.json)
    """

    def __init__(self,
                 name: str,
                 channel: str,
                 neighbor_ids: List[str],
                 channel_by_node: Dict[str, str],
                 algorithm: str = "lsr",
                 transport: str = "redis",
                 redis_cfg: Optional[Dict[str, Any]] = None):
        # Identidad interna (A/B/C) y canal propio
        self.name = name
        self.channel = channel

        # Vecinos lógicos (IDs internos A/B/C); los canales se buscan en channel_by_node
        self.neighbor_ids = list(neighbor_ids)
        self.channel_by_node = dict(channel_by_node)

        # --- Modo interoperabilidad: usar canales en from/to "en el cable"
        # Si True: al ENVIAR usamos canales; al RECIBIR convertimos de canal -> ID interno (A/B/C).
        self.use_channel_ids_on_wire: bool = True
        # Mapa inverso: canal -> ID interno
        self.rev_channel: Dict[str, str] = {ch: nid for nid, ch in self.channel_by_node.items()}

        # Algoritmo por defecto (lsr|flooding|dvr)
        self.algorithm = (algorithm or "lsr").lower()
        self.transport = transport
        self.redis_cfg = redis_cfg or {}

        # Grafo global (para LSR) y tabla de ruteo común
        self.graph: Dict[str, Dict[str, float]] = {}      # u -> {v: cost}
        self.route_table: Dict[str, str] = {}             # dst -> next_hop

        # Costos locales hacia mis vecinos (si no hay pesos, 1.0)
        self.link_costs: Dict[str, float] = {v: 1.0 for v in self.neighbor_ids}

        # Colas / control
        self.incoming: "queue.Queue[Message]" = queue.Queue()
        self.running = False

        # Deduplicación
        # - LSR: dedup por (src, alg, seq)
        self.seen_info_seq: Set[Tuple[str, str, int]] = set()
        # - Flooding/data: dedup por id de mensaje
        self.seen_msg_ids: Set[str] = set()

        # Métricas HELLO/ECHO
        self.neigh_last_seen: Dict[str, float] = {}
        self.neigh_last_hello: Dict[str, float] = {}
        self.neighbor_rtt_ms: Dict[str, float] = {}

        # Estado de vecinos (up/down)
        self._neighbor_status: Dict[str, bool] = {v: True for v in self.neighbor_ids}

        # Secuencias INFO
        self.lsr_seq = 0
        self.dvr_seq = 0  # DV no requiere seq estrictamente

        # DVR engine
        self.dvr = DistanceVectorRouter(self.name, split_horizon=True, poison_reverse=False)

        # Hilos
        self.t_recv: Optional[threading.Thread] = None
        self.t_forward: Optional[threading.Thread] = None
        self.t_hello: Optional[threading.Thread] = None
        self.t_routing: Optional[threading.Thread] = None
        self.t_watch: Optional[threading.Thread] = None

        # Transporte (Redis)
        self.redis = None
        self.pubsub = None

    # ---------------- Helpers de IDs en el cable ----------------
    def _wire_id(self, nid: str) -> str:
        """Devuelve el ID para poner 'en el cable': canal si está activo el modo; si no, el mismo nid."""
        if not self.use_channel_ids_on_wire:
            return nid
        return self.channel_by_node.get(nid, nid)

    def _internal_id(self, wire: str) -> str:
        """Convierte del ID en el cable (canal) a ID interno A/B/C; si no existe, deja como viene."""
        if not self.use_channel_ids_on_wire:
            return wire
        return self.rev_channel.get(wire, wire)

    def _normalize_incoming_ids(self, m: Message):
        """Convierte src/dst de canal -> ID interno en mensajes entrantes."""
        if self.use_channel_ids_on_wire:
            if m.src:
                m.src = self._internal_id(m.src)
            if m.dst and m.dst != "*":
                m.dst = self._internal_id(m.dst)

    # ---------------- Ciclo de vida ----------------
    def start(self):
        self.running = True

        # Inicializa grafo con enlaces locales (para LSR)
        self.graph.setdefault(self.name, {})
        for v in self.neighbor_ids:
            c = float(self.link_costs.get(v, 1.0))
            self.graph.setdefault(self.name, {})[v] = c
            self.graph.setdefault(v, {})[self.name] = c

        # Inicializa DVR con enlaces directos
        for v in self.neighbor_ids:
            self.dvr.update_direct_link(v, float(self.link_costs.get(v, 1.0)))
        # Inicializa route_table desde el algoritmo activo
        if self.algorithm == "dvr":
            self.route_table = self.dvr.get_routing_table()

        if self.transport == "redis":
            self._redis_connect()

        self.t_recv = threading.Thread(target=self._recv_loop, daemon=True)
        self.t_forward = threading.Thread(target=self._forwarding_loop, daemon=True)
        self.t_hello = threading.Thread(target=self._hello_loop, daemon=True)
        self.t_routing = threading.Thread(target=self._routing_loop, daemon=True)
        self.t_watch = threading.Thread(target=self._neighbor_watch_loop, daemon=True)

        self.t_recv.start()
        self.t_forward.start()
        self.t_hello.start()
        self.t_routing.start()
        self.t_watch.start()

        print(f"{CC.G}[{self.name}] started{CC.RESET} | alg={self.algorithm} | channel={self.channel} | neighbors={self.neighbor_ids}")

    def stop(self):
        self.running = False
        for t in [self.t_recv, self.t_forward, self.t_hello, self.t_routing, self.t_watch]:
            if t:
                t.join(timeout=1.0)
        self._redis_close()
        print(f"{CC.GR}[{self.name}] stopped.{CC.RESET}")

    # ---------------- Transporte: Redis ----------------
    def _redis_connect(self):
        import redis  # pip install redis
        kwargs = {
            "host": self.redis_cfg.get("host", "localhost"),
            "port": int(self.redis_cfg.get("port", 6379)),
            "password": self.redis_cfg.get("password", ""),
            "decode_responses": True,
        }
        if "username" in self.redis_cfg and self.redis_cfg["username"]:
            kwargs["username"] = self.redis_cfg["username"]
        self.redis = redis.Redis(**kwargs)
        try:
            self.redis.ping()
        except Exception as e:
            print(f"[{self.name}] Redis ping error: {e}")

        self.pubsub = self.redis.pubsub()
        self.pubsub.subscribe(self.channel)

    def _redis_close(self):
        try:
            if self.pubsub:
                self.pubsub.close()
        except Exception:
            pass

    def _safe_publish(self, ch: str, data: str, retry: int = 1) -> bool:
        """Publica con un intento de reconexión en caso de fallo."""
        try:
            self.redis.publish(ch, data)
            return True
        except Exception:
            try:
                # reconectar y reintentar una vez
                self._redis_close()
                self._redis_connect()
                if retry > 0:
                    self.redis.publish(ch, data)
                    return True
            except Exception:
                pass
        return False

    def _recv_loop(self):
        if self.transport != "redis":
            print(f"[{self.name}] transport '{self.transport}' no implementado (usa redis).")
            return
        while self.running:
            try:
                msg = self.pubsub.get_message(timeout=1.0)
                if not msg or msg.get("type") != "message":
                    time.sleep(0.01)
                    continue
                raw = msg.get("data")
                try:
                    m = Message.from_json(raw)
                except Exception as e:
                    print(f"[{self.name}] invalid json: {e}")
                    continue
                # Normaliza src/dst de canal -> ID interno
                self._normalize_incoming_ids(m)
                self.incoming.put(m)
            except Exception:
                # Re-suscribir en caso de error
                try:
                    self._redis_close()
                    self._redis_connect()
                except Exception:
                    time.sleep(0.5)

    # ---------------- Envío ----------------
    def _channel_of(self, dst_node: str) -> str:
        """Canal del nodo destino (usa IDs internos A/B/C)."""
        return self.channel_by_node.get(dst_node, dst_node)

    def send_to_node(self, dst_node: str, m: Message):
        """Publica en el canal del nodo dst_node (con reconexión básica)."""
        if self.transport != "redis":
            print(f"[{self.name}] send_to_node requiere transport=redis")
            return
        ch = self._channel_of(dst_node)
        ok = self._safe_publish(ch, m.to_json())
        if not ok:
            print(f"[{self.name}] publish FALLÓ a {dst_node} ({ch}) — reintento agotado")

    def send_data(self, dst_node: str, payload: Any, hops: int = 16, alg: str = None):
        """
        Enviar dato de usuario.
        - Broadcast: dst_node == "*" => flooding a todos los vecinos (to="*").
        - Unicast:
            - LSR/DVR: usa route_table para next-hop; si no hay ruta, fallback a flooding.
            - Flooding: envía a todos los vecinos.
        """
        if alg is None:
            alg = self.algorithm

        wire_src = self._wire_id(self.name)

        if dst_node == "*":
            m = Message.data(src=wire_src, dst="*", payload=payload, hops=hops, alg="flooding")
            for v in self.neighbor_ids:
                self.send_to_node(v, m)
            return

        wire_dst = self._wire_id(dst_node)  # destino final en el cable (canal si corresponde)
        m = Message.data(src=wire_src, dst=wire_dst, payload=payload, hops=hops, alg=alg)

        if alg in ("lsr", "dvr"):
            nh = self.route_table.get(dst_node)  # next-hop por ID interno
            if nh:
                self.send_to_node(nh, m)
            else:
                # sin ruta -> flooding controlado
                m.headers["alg"] = "flooding"
                for v in self.neighbor_ids:
                    self.send_to_node(v, m)
        else:
            # flooding
            for v in self.neighbor_ids:
                self.send_to_node(v, m)

    # ---------------- Utilidades/Comandos ----------------
    def send_hello(self, dst: str, hops: int = 4):
        """PING: envía HELLO a un vecino específico para medir RTT con el ECHO."""
        h = Message.hello(src=self._wire_id(self.name), dst=self._wire_id(dst), hops=hops, alg="flooding")
        self.neigh_last_hello[dst] = time.time()
        self.send_to_node(dst, h)

    def get_lsdb(self) -> Dict[str, Dict[str, float]]:
        """Devuelve una copia de la Link-State Database (grafo con costos)."""
        return {u: dict(nbrs) for u, nbrs in self.graph.items()}

    # ---------------- Hilos funcionales ----------------
    def _hello_loop(self):
        while self.running:
            now = time.time()
            for v in self.neighbor_ids:
                h = Message.hello(src=self._wire_id(self.name), dst=self._wire_id(v), hops=4, alg="flooding")
                self.neigh_last_hello[v] = now
                self.send_to_node(v, h)
            time.sleep(2.0)  # periodo HELLO

    def _routing_loop(self):
        while self.running:
            if self.algorithm == "lsr":
                self._emit_info_lsr()
                self._recompute_routes()
            elif self.algorithm == "dvr":
                self._emit_info_dvr()
                # tabla DV ya se refresca al recibir; aquí solo reflejamos por si acaso
                self.route_table = self.dvr.get_routing_table()
            # flooding no requiere cómputo periódico
            time.sleep(3.0)

    def _neighbor_watch_loop(self):
        """Monitorea vecinos; si cambian (up/down), actualiza grafo/DV y emite anuncios."""
        THRESH = 6.0  # segundos sin ver ECHO/HELLO => down
        while self.running:
            now = time.time()
            changed = False
            for v in list(self.neighbor_ids):
                last = self.neigh_last_seen.get(v, 0.0)
                up = (now - last) <= THRESH
                if up != self._neighbor_status.get(v, False):
                    self._neighbor_status[v] = up
                    changed = True
                    if not up:
                        # vecino cae
                        self.graph.get(self.name, {}).pop(v, None)
                        self.graph.get(v, {}).pop(self.name, None)
                        # DV: enlace directo eliminado
                        self.dvr.update_direct_link(v, None)
                        print(f"{CC.R}[{self.name}] vecino DOWN: {v}{CC.RESET}")
                    else:
                        # vecino vuelve
                        c = float(self.link_costs.get(v, 1.0))
                        self.graph.setdefault(self.name, {})[v] = c
                        self.graph.setdefault(v, {})[self.name] = c
                        # DV: enlace directo restaurado
                        self.dvr.update_direct_link(v, c)
                        print(f"{CC.G}[{self.name}] vecino UP: {v}{CC.RESET}")
            if changed:
                if self.algorithm == "lsr":
                    self._emit_info_lsr()  # anuncia cambio
                elif self.algorithm == "dvr":
                    # Triggered update
                    self._emit_info_dvr()
                    self.route_table = self.dvr.get_routing_table()
            time.sleep(1.0)

    def _forwarding_loop(self):
        while self.running:
            try:
                m: Message = self.incoming.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                # (IDs ya normalizados a A/B/C en _recv_loop)
                if m.type == Message.TYPE_HELLO:
                    self._handle_hello(m)
                    continue

                if m.type == Message.TYPE_ECHO:
                    self._handle_echo(m)
                    continue

                if m.type == Message.TYPE_INFO:
                    self._handle_info(m)
                    continue

                if m.type == Message.TYPE_MESSAGE:
                    # Entrega local: acepta "*" o mi ID interno; por compat, también mi canal
                    if m.dst == "*" or m.dst == self.name or m.dst == self.channel:
                        self._deliver(m)
                        continue

                    # Dedup en flooding (por id)
                    if m.alg() == "flooding":
                        mid = m.headers.get("id")
                        if mid in self.seen_msg_ids:
                            continue
                        self.seen_msg_ids.add(mid)

                    # TTL
                    m.dec_hops()
                    if m.hops <= 0:
                        continue

                    # Reenvío
                    if m.alg() in ("lsr", "dvr"):
                        # m.dst puede venir como canal de destino; conviértelo a ID interno si aplica
                        dst_internal = self._internal_id(m.dst)
                        nh = self.route_table.get(dst_internal)
                        if nh:
                            self.send_to_node(nh, m)
                        else:
                            # sin ruta, flooding de respaldo
                            for v in self.neighbor_ids:
                                self.send_to_node(v, m)
                    else:  # flooding
                        for v in self.neighbor_ids:
                            self.send_to_node(v, m)
            except Exception as e:
                print(f"[{self.name}] forwarding error: {e}")

    # ---------------- Handlers ----------------
    def _handle_hello(self, m: Message):
        # Responder ECHO conservando algoritmo (IDs en el cable como canales si corresponde)
        e = Message.echo(src=self._wire_id(self.name), dst=self._wire_id(m.src), hops=4, alg=m.alg())
        self.send_to_node(m.src, e)  # aquí m.src ya es ID interno; publish por su canal

    def _handle_echo(self, m: Message):
        now = time.time()
        ts = float(m.headers.get("ts", now))
        self.neigh_last_seen[m.src] = now
        self.neighbor_rtt_ms[m.src] = max(0.0, (now - ts) * 1000.0)

    def _emit_info_lsr(self, only_to: Optional[str] = None):
        """Emite LSP (INFO) con mis vecinos y pesos locales. Si only_to se da, se envía solo allí."""
        self.lsr_seq += 1
        # vecinos con claves "en el cable" (canales) si corresponde
        neighbors_wire: Dict[str, float] = {}
        for v in self.neighbor_ids:
            neighbors_wire[self._wire_id(v)] = float(self.link_costs.get(v, 1.0))

        targets = [only_to] if only_to else self.neighbor_ids
        for v in targets:
            if not v:
                continue
            # 🚫 sin dst=..., porque tu Message.info_lsr no lo acepta
            lsp = Message.info_lsr(
                src=self._wire_id(self.name),
                seq_num=self.lsr_seq,
                neighbors=neighbors_wire,
                hops=16,
                alg="lsr",
            )
            # ✅ si quieres que el JSON “en el cable” también tenga "to", puedes setearlo después:
            lsp.dst = self._wire_id(v)   # opcional; si no lo pones, igual solo lo recibe ese vecino por el canal
            self.send_to_node(v, lsp)


    def _emit_info_dvr(self):
        """Emite vector de distancias (INFO alg='dvr') a cada vecino (split-horizon por-vecino)."""
        self.dvr_seq += 1
        for v in self.neighbor_ids:
            vec = self.dvr.announce_for(v)  # split-horizon/poison-reverse
            vec_wire = {self._wire_id(d): dist for d, dist in (vec or {}).items()}
            msg = Message.info_lsr(
                src=self._wire_id(self.name),
                seq_num=self.dvr_seq,
                neighbors=vec_wire,
                hops=16,
                alg="dvr",
            )
            msg.dst = self._wire_id(v)   # opcional
            self.send_to_node(v, msg)


    def _handle_info(self, m: Message):
        """Procesa INFO: LSR (LSP) o DVR (vector).  (m.src/m.dst ya son IDs internos por normalización)"""
        alg = m.alg()
        if alg == "lsr":
            key = (m.src, alg, int(m.seq_num or 0))
            if key in self.seen_info_seq:
                return
            self.seen_info_seq.add(key)

            # Actualiza grafo con la vista del origen (costos normalizados)
            u = m.src
            self.graph.setdefault(u, {})
            self.graph[u] = {}

            # neighbors puede venir como lista (peso=1.0) o dict {canal/ID: costo}
            neigh_obj = m.neighbors or {}
            if isinstance(neigh_obj, list):
                for w in neigh_obj:
                    v = self._internal_id(str(w))
                    c = float(self.link_costs.get(v, 1.0))
                    self.graph[u][v] = c
                    self.graph.setdefault(v, {})
                    self.graph[v].setdefault(u, c)
            else:
                for w, cost in neigh_obj.items():
                    v = self._internal_id(str(w))
                    c = float(cost)
                    self.graph[u][v] = c
                    self.graph.setdefault(v, {})
                    self.graph[v].setdefault(u, c)

            # Repropagar si queda TTL (LSR propaga LSPs)
            m.dec_hops()
            if m.hops > 0:
                for v in self.neighbor_ids:
                    if v != m.src:  # evita eco inmediato trivial
                        self.send_to_node(v, m)
            # Recalcular rutas
            self._recompute_routes()
            return

        if alg == "dvr":
            # DV no repropaga; procesa vector recibido
            vec = m.neighbors or {}
            # Convierte claves canal->ID interno
            vec_internal = {self._internal_id(str(d)): float(dist) for d, dist in vec.items()}
            self.dvr.receive_announcement(m.src, vec_internal)
            self.route_table = self.dvr.get_routing_table()
            return

    def _recompute_routes(self):
        """Corre Dijkstra y reconstruye tabla dst->next_hop (para LSR)."""
        for u in list(self.graph.keys()):
            self.graph.setdefault(u, {})
        dist, prev = dijkstra(self.graph, self.name)
        table: Dict[str, str] = {}
        for dst in dist.keys():
            if dst == self.name:
                continue
            # reconstruye primer salto
            cur = dst
            prv = prev.get(cur)
            if not prv:
                continue
            while prv and prv != self.name:
                cur = prv
                prv = prev.get(cur)
            table[dst] = cur
        self.route_table = table
        # print(f"[{self.name}] routes: {self.route_table}")

    # ---------------- Entrega local ----------------
    def _deliver(self, m):
        try:
            print(f"{CC.C}📬 [{self.name}] DATA de {m.src or '?'} -> {m.dst}: {m.payload}{CC.RESET}")
        except NameError:
            print(f"[{self.name}] DATA de {m.src} -> {m.dst}: {m.payload}")

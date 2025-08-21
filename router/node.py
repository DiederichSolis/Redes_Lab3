from __future__ import annotations

import json
import queue
import random
import socket
import threading
import time
from typing import Dict, Any, Set, Tuple

try:
    from .message import Message, Protocol, MessageType
    from .dijkstra import dijkstra
    from .dvr import DistanceVectorRouter
    from .metrics import collector
except Exception:
    from message import Message, Protocol, MessageType
    from dijkstra import dijkstra
    from dvr import DistanceVectorRouter
    from metrics import collector

BUF = 65535


class Node:

    # ---------------- init / infra ----------------
    def __init__(
        self,
        name: str,
        bind_host: str,
        bind_port: int,
        names: Dict[str, Dict[str, int]],
        neighbors: list[str],
        routing_protocol: str = "lsr",  # "lsr" o "dvr"
    ) -> None:
        self.name = name
        self.host = bind_host
        self.port = bind_port
        self.addr = (bind_host, bind_port)
        self.routing_protocol = routing_protocol

        # {"A":{"host":"127.0.0.1","port":56001}, ...}
        self.names: Dict[str, Dict[str, int]] = names
        # Vecinos iniciales 
        self.neighbors: Set[str] = set(neighbors)

        # Grafo dirigido con pesos: {u: {v: w, ...}, ...}
        self.graph: Dict[str, Dict[str, float]] = {}
        self.graph[self.name] = {}
        for v in self.neighbors:
            self.graph[self.name][v] = 1.0  # costo base

        # Tabla de ruteo calculada desde self.graph
        # Estructura: {dest: next_hop}
        self.routing_table: Dict[str, str] = {}

        # Control de duplicados para LSR y Flooding
        self.seen_lsp_ids: Set[str] = set()
        self.seen_flood_ids: Set[str] = set()

        # RTT simple por vecino 
        self.neighbor_rtt_ms: Dict[str, float] = {}

        # Colas / sincronización
        self.incoming: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()

        # Socket UDP
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(self.addr)
        self.sock.settimeout(0.5)

        # Inicializar router DVR si es necesario
        self.dvr_router = None
        if self.routing_protocol == "dvr":
            self.dvr_router = DistanceVectorRouter(self.name)
            # Configurar enlaces directos iniciales
            for v in self.neighbors:
                self.dvr_router.update_direct_link(v, 1.0)

        # Registrar protocolo para métricas
        collector.register_protocol(self.routing_protocol, self.name)

    def start(self) -> None:
        self.t_listener = threading.Thread(target=self._listener, daemon=True)
        self.t_forward = threading.Thread(target=self._forwarding_loop, daemon=True)
        self.t_routing = threading.Thread(target=self._routing_loop, daemon=True)
        self.t_hello = threading.Thread(target=self._hello_loop, daemon=True)

        self.t_listener.start()
        self.t_forward.start()
        self.t_routing.start()
        self.t_hello.start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.sock.close()
        except Exception:
            pass

    # ---------------- envío ----------------
    def send_raw(self, logical_name: str, json_str: str) -> None:
        """Envía el json_str al nodo 'logical_name' vía UDP usando self.names."""
        cfg = self.names.get(logical_name)
        if not cfg:
            print(f"[{self.name}] send_raw: destino desconocido: {logical_name}")
            return
        addr = (cfg["host"], int(cfg["port"]))
        try:
            self.sock.sendto(json_str.encode("utf-8"), addr)
            # Registrar métrica de mensaje enviado
            collector.record_message(self.routing_protocol, "unknown", self.name, logical_name)
        except Exception as e:
            print(f"[{self.name}] ERROR sendto({logical_name} {addr}): {e}")

    def send(self, m: Message) -> None:
        """Envía el mensaje m según su protocolo y tabla de ruteo."""
        # Registrar métrica de mensaje
        collector.record_message(m.proto, m.type, m.src, m.dst)
        
        if m.proto == Protocol.LSR and m.type == MessageType.DATA:
            nh = self._next_hop_for(m.dst)
            if nh is None:
                # Fallback: intenta enviar a todos los vecinos
                for v in self.neighbors:
                    self.send_raw(v, m.to_json())
                return
            self.send_raw(nh, m.to_json())
            return

        if m.proto == Protocol.DVR and m.type == MessageType.DATA:
            if self.dvr_router:
                nh = self.dvr_router.get_next_hop(m.dst)
                if nh is None:
                    # Fallback: intenta enviar a todos los vecinos
                    for v in self.neighbors:
                        self.send_raw(v, m.to_json())
                    return
                self.send_raw(nh, m.to_json())
                return

        # Si 'to' es vecino, envía directo. Si no, broadcast de cortesía.
        if m.dst in self.neighbors:
            self.send_raw(m.dst, m.to_json())
        else:
            for v in self.neighbors:
                self.send_raw(v, m.to_json())

    # ---------------- listener ----------------
    def _listener(self) -> None:
        while not self.stop_event.is_set():
            try:
                data, _addr = self.sock.recvfrom(BUF)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"[{self.name}] listener error: {e}")
                continue

            try:
                raw = data.decode("utf-8")
            except Exception:
                continue
            # Empuja crudo; se parsea en el forwarding loop
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

            # --- HELLO/ECHO (liveness/RTT simple) ---
            if m.type == MessageType.HELLO:
                # responde con echo si soy destino
                if m.dst == self.name:
                    echo = Message(
                        proto=m.proto or Protocol.SYSTEM,
                        type=MessageType.ECHO,
                        src=self.name,
                        dst=m.src,
                        ttl=8,
                        headers={"t0": m.headers.get("t0", 0.0)},
                        payload={},
                    )
                    # echo va directo al origen 
                    self.send(echo)
                    # Registrar métrica de hello/echo
                    collector.record_message(m.proto or Protocol.SYSTEM, MessageType.HELLO, m.src, m.dst)
                continue

            if m.type == MessageType.ECHO and m.dst == self.name:
                t0 = float(m.headers.get("t0", 0.0))
                rtt_ms = max(0.0, (time.time() - t0) * 1000.0)
                self.neighbor_rtt_ms[m.src] = rtt_ms
                # self._set_link_cost(self.name, m.src, max(1.0, rtt_ms / 50.0)) # actualiza costo del enlace
                # Registrar métrica de echo
                collector.record_message(m.proto or Protocol.SYSTEM, MessageType.ECHO, m.src, m.dst)
                continue

            # --- DVR: Distance Vector Announcements ---
            if m.proto == Protocol.DVR and m.type == MessageType.DV_ANNOUNCEMENT:
                if self.dvr_router:
                    distances = m.payload.get("distances", {})
                    self.dvr_router.receive_announcement(m.src, distances)
                    print(f"[{self.name}] DVR: Recibido anuncio de {m.src}: {distances}")
                    # Registrar métrica de mensaje recibido
                    collector.record_message(m.proto, m.type, m.src, m.dst)
                continue

            # --- LSR: LSP reception/flood ---
            if m.proto == Protocol.LSR and m.type == MessageType.LSP:
                # Registrar métrica de LSP recibido
                collector.record_message(Protocol.LSR, MessageType.LSP, m.src, m.dst)
                
                lsp: Dict[str, Any] = m.payload or {}
                lsp_id = str(lsp.get("id", ""))
                node = str(lsp.get("node", ""))
                links = lsp.get("links", {}) or {}

                if not lsp_id or lsp_id in self.seen_lsp_ids:
                    continue
                self.seen_lsp_ids.add(lsp_id)

                # Asegurar nodos en el grafo
                if node not in self.graph:
                    self.graph[node] = {}
                # Actualizar enlaces de ese nodo
                self.graph[node].update({str(k): float(v) for k, v in links.items()})

                # Flood a todos los vecinos menos de donde vino
                came_from = m.headers.get("came_from")
                for v in self.neighbors:
                    if v == came_from:
                        continue
                    fwd = Message(
                        proto=Protocol.LSR,
                        type=MessageType.LSP,
                        src=self.name,
                        dst=v,
                        ttl=m.ttl,
                        headers={"came_from": self.name},
                        payload=lsp,
                    )
                    self.send_raw(v, fwd.to_json())
                    # Registrar métrica de LSP reenviado
                    collector.record_message(Protocol.LSR, MessageType.LSP, self.name, v)
                continue

            # --- FLOODING: DATA ---
            if m.proto == Protocol.FLOODING and m.type == MessageType.DATA:
                # Registrar métrica de flooding recibido
                collector.record_message(Protocol.FLOODING, MessageType.DATA, m.src, m.dst)
                
                msg_id = str(m.headers.get("id", ""))
                came_from = m.headers.get("came_from")
                if not msg_id:
                    # sin id no podemos suprimir duplicados → descarta
                    continue
                if msg_id in self.seen_flood_ids:
                    continue
                self.seen_flood_ids.add(msg_id)

                # entrega si soy destino
                if m.dst == self.name:
                    self._deliver(m)
                    continue

                # reenvía a todos menos de quien vino
                for v in self.neighbors:
                    if v == came_from:
                        continue
                    fwd = Message(
                        proto=Protocol.FLOODING,
                        type=MessageType.DATA,
                        src=self.name,
                        dst=m.dst,
                        ttl=m.ttl,
                        headers={"id": msg_id, "came_from": self.name},
                        payload=m.payload,
                    )
                    self.send_raw(v, fwd.to_json())
                    # Registrar métrica de flooding
                    collector.record_message(Protocol.FLOODING, MessageType.DATA, self.name, v)
                continue

            # --- DATA (LSR u otros) ---
            if m.type == MessageType.DATA:
                # Registrar métrica de data recibido
                collector.record_message(m.proto, MessageType.DATA, m.src, m.dst)
                
                if m.dst == self.name:
                    self._deliver(m)
                else:
                    self.send(m)
                continue

            # --- INFO u otros ---
            if m.type == MessageType.INFO and m.dst == self.name:
                print(f"[{self.name}] INFO: {m.payload}")
                # Registrar métrica de info
                collector.record_message(m.proto, MessageType.INFO, m.src, m.dst)
                continue

    # ---------------- routing (Dijkstra + LSP emit) ----------------
    def _routing_loop(self) -> None:
        """
        1) Recalcula la tabla de ruteo periódicamente.
        2) Emite cada unos segundos un LSP con sus enlaces actuales (LSR) o anuncio DVR.
        """
        next_lsp_at = 0.0
        next_dv_at = 0.0
        while not self.stop_event.is_set():
            now = time.time()

            # Asegúrate de que existo en el grafo
            if self.name not in self.graph:
                self.graph[self.name] = {}
            # Mantén enlaces a vecinos (si no existían)
            for v in list(self.neighbors):
                self.graph.setdefault(self.name, {}).setdefault(v, 1.0)

            # Recalcular rutas según el protocolo
            if self.routing_protocol == "lsr":
                self._update_routing_table()
                # Emitir LSP cada 3s (simple)
                if now >= next_lsp_at:
                    self._emit_lsp()
                    next_lsp_at = now + 3.0
            elif self.routing_protocol == "dvr":
                # Actualizar tabla de enrutamiento desde DVR
                self._update_routing_table()
                # Emitir anuncios DVR cada 2s
                if now >= next_dv_at:
                    self._emit_dv_announcement()
                    next_dv_at = now + 2.0

            time.sleep(1.0)

    def _recompute_routes(self) -> None:
        """Ejecuta Dijkstra y construye self.routing_table."""
        # Asegura simetría mínima 
        for u, nbrs in list(self.graph.items()):
            if u not in self.graph:
                self.graph[u] = {}
            for v in list(nbrs.keys()):
                self.graph.setdefault(v, {})

        try:
            dist, prev = dijkstra(self.graph, self.name)  # type: ignore
        except Exception as e:
            # Si hay algún problema en dijkstra(), deja la tabla como está
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

    def _update_routing_table(self) -> None:
        """Actualiza la tabla de enrutamiento según el protocolo configurado."""
        if self.routing_protocol == "lsr":
            self._recompute_routes()
        elif self.routing_protocol == "dvr" and self.dvr_router:
            # Para DVR, la tabla se actualiza automáticamente en el router DVR
            self.routing_table = self.dvr_router.get_routing_table()

    @staticmethod
    def _first_hop_from_prev(prev: Dict[str, str | None], dest: str) -> str | None:
        """
        Retorna el vecino inmediato (next hop) hacia dest usando la lista de predecesores 'prev'.
        Si no hay ruta, devuelve None.
        """
        cur = dest
        seen = 0
        while prev.get(cur) is not None:
            p = prev[cur]
            if p is None:
                return None
            # Si el predecesor no tiene predecesor, entonces p es source → primer salto es cur
            if prev.get(p) is None:
                # p es source; el first hop es cur
                return cur
            cur = p
            seen += 1
            if seen > 10000:
                break
        return None

    def _emit_lsp(self) -> None:
        """Emite un LSP propio a todos los vecinos (con costo actual de los enlaces)."""
        links = {v: float(self.graph.get(self.name, {}).get(v, 1.0)) for v in self.neighbors}
        lsp = {
            "id": f"{self.name}-{int(time.time() * 1000)}-{random.randint(0, 9999)}",
            "node": self.name,
            "links": links,
        }
        for v in list(self.neighbors):
            m = Message(
                proto=Protocol.LSR,
                type=MessageType.LSP,
                src=self.name,
                dst=v,
                ttl=8,
                headers={"came_from": self.name},
                payload=lsp,
            )
            self.send_raw(v, m.to_json())
            # Registrar métrica de LSP enviado
            collector.record_message(Protocol.LSR, MessageType.LSP, self.name, v)

    def _emit_dv_announcement(self) -> None:
        """Emite anuncio de distancias DVR a todos los vecinos."""
        if not self.dvr_router:
            return
            
        # Obtener tabla de distancias actual
        distances = self.dvr_router.announce_distances()
        
        # Agregar enlaces directos
        for neighbor in self.neighbors:
            distances[neighbor] = 1.0  # Costo directo
            
        # Enviar anuncio a todos los vecinos
        for v in list(self.neighbors):
            m = Message(
                proto=Protocol.DVR,
                type=MessageType.DV_ANNOUNCEMENT,
                src=self.name,
                dst=v,
                ttl=8,
                headers={},
                payload={"distances": distances},
            )
            self.send_raw(v, m.to_json())
            # Registrar métrica de anuncio DVR enviado
            collector.record_message(Protocol.DVR, MessageType.DV_ANNOUNCEMENT, self.name, v)
            print(f"[{self.name}] DVR: Enviando anuncio a {v}: {distances}")

    # ---------------- HELLO loop ----------------
    def _hello_loop(self) -> None:
        """Envía HELLO periódico a los vecinos para medir liveness/RTT simple."""
        while not self.stop_event.is_set():
            t0 = time.time()
            for v in list(self.neighbors):
                hello = Message(
                    proto=Protocol.SYSTEM,
                    type=MessageType.HELLO,
                    src=self.name,
                    dst=v,
                    ttl=4,
                    headers={"t0": t0},
                    payload={},
                )
                self.send(hello)
                # Registrar métrica de hello enviado
                collector.record_message(Protocol.SYSTEM, MessageType.HELLO, self.name, v)
            time.sleep(2.0)

    # ---------------- utils de entrega/usuario ----------------
    def _deliver(self, m: Message) -> None:
        """Entrega local de datos al 'usuario' del nodo (por ahora, print)."""
        print(f"[{self.name}] DATA entregado de {m.src} → {m.dst} | payload={json.dumps(m.payload, ensure_ascii=False)}")

    def send_data(self, dst: str, text: str, ttl: int = 12) -> None:
        """Envío de DATA de usuario usando el protocolo configurado."""
        proto = Protocol.DVR if self.routing_protocol == "dvr" else Protocol.LSR
        m = Message(proto=proto, type=MessageType.DATA, src=self.name, dst=dst, ttl=ttl, payload={"text": text})
        # Registrar métrica de data enviado
        collector.record_message(proto, MessageType.DATA, self.name, dst)
        self.send(m)

    def send_data_flood(self, dst: str, text: str, ttl: int = 12) -> None:
        """Envío de DATA de usuario usando Flooding 'standalone' con supresión de duplicados."""
        msg_id = f"{self.name}-{int(time.time() * 1000)}-{random.randint(0, 9999)}"
        m = Message(
            proto=Protocol.FLOODING,
            type=MessageType.DATA,
            src=self.name,
            dst=dst,
            ttl=ttl,
            headers={"id": msg_id, "came_from": self.name},
            payload={"text": text},
        )
        # Sale a todos los vecinos (primer inundación)
        for v in list(self.neighbors):
            self.send_raw(v, m.to_json())
            # Registrar métrica de flooding enviado
            collector.record_message(Protocol.FLOODING, MessageType.DATA, self.name, v)

    def send_data_dvr(self, dst: str, text: str, ttl: int = 12) -> None:
        """Envío de DATA específicamente usando DVR."""
        m = Message(proto=Protocol.DVR, type=MessageType.DATA, src=self.name, dst=dst, ttl=ttl, payload={"text": text})
        # Registrar métrica de data DVR enviado
        collector.record_message(Protocol.DVR, MessageType.DATA, self.name, dst)
        self.send(m)

    # ---------------- helpers opcionales ----------------
    def _set_link_cost(self, u: str, v: str, cost: float) -> None:
        """Ajusta el costo del enlace u->v en el grafo (crea nodos si no existen)."""
        self.graph.setdefault(u, {})
        self.graph.setdefault(v, {})
        self.graph[u][v] = float(cost)

    def _next_hop_for(self, dest: str) -> str | None:
        """Obtiene next hop desde la tabla de ruteo actual."""
        if dest == self.name:
            return self.name
        return self.routing_table.get(dest)

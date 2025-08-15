from __future__ import annotations
import socket, threading, json, time, queue, random
from typing import Dict, Any, Set
from .message import Message
from .dijkstra import dijkstra, next_hop_for

BUF = 65535

class Node:
    def __init__(self, name: str, bind_host: str, bind_port: int, names: Dict[str, Dict[str,int]], neighbors: list[str]):
        self.name = name
        self.host = bind_host
        self.port = bind_port
        self.addr = (bind_host, bind_port)
        self.names = names                  # {"A":{"host":"127.0.0.1","port":5001}, ...}
        self.neighbors = set(neighbors)     # vecinos conocidos por archivo de topo (para demo/init)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(self.addr)

        # Estado Link-State: base de topología aprendida
        # graph["A"] = {"B":1,"C":1}
        self.graph: Dict[str, Dict[str,float]] = {}
        self.graph[self.name] = {}
        for v in self.neighbors:
            self.graph[self.name][v] = 1.0

        # Tabla de ruteo (next hop por destino) derivada de Dijkstra
        self.routing_table: Dict[str, str] = {}

        # Hilos y colas
        self.incoming = queue.Queue()
        self.stop_event = threading.Event()

        # para control de LSP duplicados
        self.seen_lsp_ids: Set[str] = set()

    # ---------------- infra ----------------
    def start(self):
        self.t_listener = threading.Thread(target=self._listener, daemon=True)
        self.t_forward = threading.Thread(target=self._forwarding_loop, daemon=True)
        self.t_routing = threading.Thread(target=self._routing_loop, daemon=True)
        self.t_hello = threading.Thread(target=self._hello_loop, daemon=True)
        self.t_listener.start()
        self.t_forward.start()
        self.t_routing.start()
        self.t_hello.start()

    def stop(self):
        self.stop_event.set()
        try: self.sock.close()
        except: pass

    def _listener(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(BUF)
                self.incoming.put(data.decode("utf-8"))
            except Exception:
                time.sleep(0.01)

    def send_raw(self, target: str, s: str):
        info = self.names[target]
        self.sock.sendto(s.encode("utf-8"), (info["host"], info["port"]))

    def send(self, m: Message):
        # Rutear según tabla si no es vecino directo o si es DATA
        if m.dst == self.name:
            self._deliver(m)
            return
        nh = None
        if m.type == "data":
            nh = self.routing_table.get(m.dst)
        # Si no hay next hop aún, pero es vecino directo, intenta directo
        if nh is None and m.dst in self.neighbors:
            nh = m.dst
        if nh is None:
            # como fallback, intenta broadcasting a vecinos (temporal, no flooding total)
            for v in self.neighbors:
                self.send_raw(v, m.to_json())
            return
        self.send_raw(nh, m.to_json())

    # ---------------- loops ----------------
    def _forwarding_loop(self):
        while not self.stop_event.is_set():
            try:
                raw = self.incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                m = Message.from_json(raw)
            except Exception as e:
                print(f"[{self.name}] paquete inválido: {raw[:60]}... err={e}")
                continue
            if m.ttl <= 0:
                continue
            # decrementar TTL
            m.ttl -= 1

            if m.type == "hello":
                # responder con echo (simple RTT)
                if m.dst == self.name:
                    echo = Message(proto=m.proto, type="echo", src=self.name, dst=m.src, headers={"t0": m.headers.get("t0")})
                    self.send(echo)
                # nada más que hacer (HELLO no se forwardea)
                continue

            if m.type == "echo" and m.dst == self.name:
                t0 = m.headers.get("t0", time.time())
                rtt = (time.time() - t0) * 1000.0
                print(f"[{self.name}] RTT con {m.src}: {rtt:.1f} ms")
                continue

            if m.type == "lsp":
                # LSP = {"id": "...", "node":"A","links":{"B":1,"C":1}}; se hace flooding a vecinos excepto quien envió
                lsp = m.payload
                lsp_id = lsp.get("id")
                if lsp_id in self.seen_lsp_ids:
                    continue
                self.seen_lsp_ids.add(lsp_id)
                node = lsp.get("node")
                links = lsp.get("links", {})
                if node not in self.graph:
                    self.graph[node] = {}
                # actualizar enlaces
                self.graph[node].update({k: float(v) for k,v in links.items()})
                # flood a vecinos (excepto devolver al emisor directo si viene en headers)
                came_from = m.headers.get("came_from")
                for v in self.neighbors:
                    if v == came_from:
                        continue
                    fwd = Message(proto="lsr", type="lsp", src=self.name, dst=v, ttl=m.ttl, headers={"came_from": self.name}, payload=lsp)
                    self.send_raw(v, fwd.to_json())
                continue

            if m.type == "data":
                if m.dst == self.name:
                    self._deliver(m)
                else:
                    # forward según tabla
                    self.send(m)
                continue

            # otros tipos: info, etc.
            if m.type == "info" and m.dst == self.name:
                print(f"[{self.name}] INFO: {m.payload}")
                continue

    def _routing_loop(self):
        # corre periódicamente Dijkstra si la topología cambió (simplificado: cada 1s)
        while not self.stop_event.is_set():
            try:
                # asegúrate de tenerte a ti mismo y enlaces a vecinos (por si hello descubriera nuevos)
                if self.name not in self.graph:
                    self.graph[self.name] = {}
                for v in list(self.neighbors):
                    self.graph[self.name][v] = 1.0

                if len(self.graph) > 0:
                    dist, prev = dijkstra(self.graph, self.name)
                    new_table: Dict[str,str] = {}
                    for dest in self.graph.keys():
                        if dest == self.name:
                            continue
                        nh = next_hop_for(dest, self.name, prev)
                        if nh:
                            new_table[dest] = nh
                    self.routing_table = new_table
                time.sleep(1.0)
            except Exception as e:
                print(f"[{self.name}] routing_loop err: {e}")
                time.sleep(1.0)

    def _hello_loop(self):
        # cada 2s envía HELLO a vecinos para medir RTT
        while not self.stop_event.is_set():
            now = time.time()
            for v in list(self.neighbors):
                h = Message(proto="lsr", type="hello", src=self.name, dst=v, headers={"t0": now})
                self.send_raw(v, h.to_json())
            # también emite tu LSP cada 3s (alternado)
            if int(now) % 3 == 0:
                self._emit_lsp()
            time.sleep(1.0)

    def _emit_lsp(self):
        # LSP propio con tus enlaces actuales
        lsp = {
            "id": f"{self.name}-{int(time.time()*1000)}-{random.randint(0,9999)}",
            "node": self.name,
            "links": {v: 1.0 for v in self.neighbors}
        }
        for v in list(self.neighbors):
            m = Message(proto="lsr", type="lsp", src=self.name, dst=v, headers={"came_from": self.name}, payload=lsp)
            self.send_raw(v, m.to_json())

    def _deliver(self, m: Message):
        print(f"[{self.name}] DATA entregado de {m.src} → {m.dst} | payload={m.payload}")

    # utilidad: enviar DATA de usuario
    def send_data(self, dst: str, text: str, ttl: int = 12):
        m = Message(proto="lsr", type="data", src=self.name, dst=dst, ttl=ttl, payload={"text": text})
        self.send(m)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any

# ---------- Bootstrap de imports (funciona con tu estructura) ----------
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from router.node import Node  # node.py está en router/
from router.metrics import collector

# ---------- Imports estándar ----------
import argparse
import json
import signal
import time
import threading
from typing import Dict, List


# ---------- Utilidades para cargar archivos ----------
def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: no existe el archivo: {p}")
        sys.exit(2)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _unwrap_config(obj: dict) -> dict:
    """
    Acepta tanto:
      { "A": {...}, "B": {...} }
    como:
      { "type": "names" | "topo", "config": { ... } }
    """
    if isinstance(obj, dict) and "config" in obj and isinstance(obj["config"], dict):
        return obj["config"]
    return obj


def load_names(path: str) -> Dict[str, Dict[str, int]]:
    """
    Estructuras aceptadas:
    1) Plano:
       {
         "A": {"host": "127.0.0.1", "port": 56001},
         "B": {"host": "127.0.0.1", "port": 56002}
       }
    2) Envuelto:
       {
         "type": "names",
         "config": {
           "A": {"host": "127.0.0.1", "port": 56001},
           "B": {"host": "127.0.0.1", "port": 56002}
         }
       }
    """
    raw = load_json(path)
    data = _unwrap_config(raw)
    names: Dict[str, Dict[str, int]] = {}
    for k, v in data.items():
        # v debe ser un dict con host/port
        host = v["host"]
        port = int(v["port"])
        names[k] = {"host": host, "port": port}
    return names


def load_topo(path: str) -> Dict[str, List[str]]:
    """
    Estructuras aceptadas:
    1) Plano:
       { "A": ["B","C"], "B": ["A","D"] }
    2) Envuelto:
       { "type": "topo", "config": { "A": ["B","C"], ... } }
    """
    raw = load_json(path)
    data = _unwrap_config(raw)
    topo: Dict[str, List[str]] = {}
    for k, v in data.items():
        topo[k] = list(v)
    return topo


# ---------- Construcción / control de nodos ----------
def build_nodes(names, topo, algo, transport="udp", redis_cfg=None):
    nodes: Dict[str, Node] = {}

    allowed = {"lsr", "dvr", "flooding"}
    default_protocol = str(algo).lower()
    if default_protocol not in allowed:
        default_protocol = "lsr"

    # Asegura que todos los que están en names existan en topo
    for n in names.keys():
        topo.setdefault(n, [])

    # Mapa lógico -> canal Redis (si no hay 'channel' en names, usa el nombre)
    channel_map = {k: str(v.get("channel", k)) for k, v in names.items()}

    for name, cfg in names.items():
        host = cfg["host"]
        port = int(cfg["port"])
        neighbors = topo.get(name, [])

        # Override opcional de protocolo en names.json
        node_protocol = str(cfg.get("protocol", default_protocol)).lower()
        if node_protocol not in allowed:
            node_protocol = default_protocol

        # redis_cfg por nodo (solo si transport=redis)
        node_redis_cfg = None
        if transport == "redis":
            base = dict(redis_cfg or {})
            base.update({
                "channel_self": channel_map[name],
                "channel_map": channel_map,
                "decode_responses": True,
            })
            node_redis_cfg = base

        nodes[name] = Node(
            name=name,
            bind_host=host,
            bind_port=port,
            names=names,
            neighbors=neighbors,
            transport=transport,
            redis_cfg=node_redis_cfg,
            routing_protocol=node_protocol,
        )

    return nodes



# ---------- Envío de mensaje de prueba ----------
def demo_send(
    algo: str,
    nodes: Dict[str, Node],
    src: str,
    dst: str,
    text: str,
    ttl: int,
) -> None:
    if src not in nodes or dst not in nodes:
        print(f"ERROR: src/dst inválidos. src={src} dst={dst} nodos={list(nodes.keys())}")
        return

    node_src = nodes[src]
    print(f"[demo] Enviando DATA {algo.upper()} {src} → {dst}: '{text}' (ttl={ttl})")

    if algo == "flooding":
        node_src.send_data_flood(dst, text, ttl=ttl)
    else:
        # LSR por defecto
        node_src.send_data(dst, text, ttl=ttl)


# ---------- Manejo de Ctrl+C ----------
def handle_sigint(nodes: Dict[str, Node]):
    def _handler(_sig, _frm):
        print("\n[demo] SIGINT recibido. Deteniendo nodos…")
        
        # Mostrar métricas finales si están disponibles
        try:
            collector.print_summary()
            
            # Generar reporte
            timestamp = int(time.time())
            report = collector.get_comparison_report()
            report_file = f"demo_metrics_{timestamp}.json"
            
            try:
                with open(report_file, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2, ensure_ascii=False)
                print(f"[demo] 💾 Métricas guardadas: {report_file}")
            except Exception as e:
                print(f"[demo] ❌ Error guardando métricas: {e}")
        except Exception as e:
            print(f"[demo] No se pudieron generar métricas: {e}")
        
        stop_nodes(nodes)
        sys.exit(0)

    return _handler

def start_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.start()
    print(f"[demo] {len(nodes)} nodos iniciados.")

def stop_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.stop()
    print("[demo] Nodos detenidos.")

def print_tables(nodes: Dict[str, Any]) -> None:
    print("\n[demo] Tablas de ruteo actuales:")
    for name in sorted(nodes.keys()):
        n = nodes[name]
        protocol = getattr(n, "routing_protocol", "unknown").upper()
        rt = getattr(n, "routing_table", {})
        print(f"  - {name} ({protocol}): {rt}")


# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Run demo Lab 3 - Routing Protocols Comparison")
    parser.add_argument("--names", default="names-sample.json", help="Ruta a archivo JSON de nombres (host/port)")
    parser.add_argument("--topo", default="topo-sample.json", help="Ruta a archivo JSON de topología")
    parser.add_argument("--algo", choices=["lsr", "dvr", "flooding"], default="lsr", help="Algoritmo para DATA")
    parser.add_argument("--src", default="A", help="Nodo origen lógico (clave en names)")
    parser.add_argument("--dst", default="D", help="Nodo destino lógico (clave en names)")
    parser.add_argument("--text", default="Hola desde demo", help="Texto a enviar")
    parser.add_argument("--ttl", type=int, default=10, help="TTL del mensaje DATA")
    parser.add_argument("--warmup", type=float, default=3.0, help="Segundos de espera antes de enviar DATA")
    parser.add_argument("--after", type=float, default=3.0, help="Segundos de espera tras enviar DATA")
    parser.add_argument("--compare", action="store_true", help="Ejecutar comparación LSR vs DVR")

    # --- flags de transporte (Parte 2) ---
    parser.add_argument("--transport", choices=["udp", "redis"], default="udp",
                        help="Medio de transporte: udp o redis (Parte 2)")
    parser.add_argument("--redis-host", default="lab3.redesuvg.cloud")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-password", default="UVGRedis2025")

    args = parser.parse_args()

    names = load_names(args.names)
    topo = load_topo(args.topo)

    # --- bloque Redis cfg (solo si transport=redis) ---
    redis_cfg = None
    if args.transport == "redis":
        redis_cfg = {
            "host": args.redis_host,
            "port": args.redis_port,
            "password": args.redis_password,
            "decode_responses": True,
        }

    if args.compare:
        # Modo comparación: crear nodos con diferentes protocolos
        print("[demo] ===== COMPARACIÓN LSR vs DVR =====")
        print("[demo] Transporte:", args.transport.upper())
        print("[demo] Nodos A, B: LSR (Dijkstra)")
        print("[demo] Nodos C, D: DVR (Bellman-Ford)")

        nodes = {}

        # A y B con LSR
        for name in ["A", "B"]:
            cfg = names[name]
            neighbors = topo.get(name, [])
            n = Node(
                name=name,
                bind_host=cfg["host"],
                bind_port=cfg["port"],
                names=names,
                neighbors=neighbors,
                transport=args.transport,
                redis_cfg=redis_cfg,
                routing_protocol="lsr",
            )
            nodes[name] = n
            n.start()

        # C y D con DVR
        for name in ["C", "D"]:
            cfg = names[name]
            neighbors = topo.get(name, [])
            n = Node(
                name=name,
                bind_host=cfg["host"],
                bind_port=cfg["port"],
                names=names,
                neighbors=neighbors,
                transport=args.transport,
                redis_cfg=redis_cfg,
                routing_protocol="dvr",
            )
            nodes[name] = n
            n.start()

        # Manejo de Ctrl+C
        signal.signal(signal.SIGINT, handle_sigint(nodes))

        try:
            print("[demo] Esperando 5s a que se propaguen LSPs/DV y se estabilicen rutas...")
            time.sleep(5)

            print("\n[demo] ===== TABLAS DE ENRUTAMIENTO =====")
            for k, n in nodes.items():
                protocol = "LSR" if n.routing_protocol == "lsr" else "DVR"
                print(f"{k} ({protocol}) → {n.routing_table}")

            print("\n[demo] ===== ENVIANDO DATOS =====")
            print("[demo] Enviando DATA A→D usando LSR...")
            nodes["A"].send_data("D", "Hola desde A usando LSR", ttl=10)

            print("[demo] Enviando DATA C→A usando DVR...")
            nodes["C"].send_data("A", "Hola desde C usando DVR", ttl=10)

            print("[demo] Enviando DATA B→C usando Flooding...")
            nodes["B"].send_data_flood("C", "Hola desde B usando Flooding", ttl=10)

            print("\n[demo] ===== MONITOREO CONTINUO =====")
            print("[demo] Presiona Ctrl+C para detener y ver métricas")
            time.sleep(2)
            print("[demo] Enviando mensajes adicionales...")
            nodes["B"].send_data("A", "Mensaje adicional B→A")
            nodes["D"].send_data("C", "Mensaje adicional D→C")
            time.sleep(1)

            cycle = 0
            while True:
                time.sleep(3)
                cycle += 1
                print(f"\n[demo] --- Ciclo {cycle} ---")
                for k, n in nodes.items():
                    protocol = "LSR" if n.routing_protocol == "lsr" else "DVR"
                    routes_count = len([r for r in n.routing_table.values() if r])
                    print(f"{k} ({protocol}): {routes_count} rutas activas")
                if cycle % 3 == 0:
                    nodes["A"].send_data("D", f"Ping ciclo {cycle}")
                    print("[demo] Enviado ping periódico A→D")

        except KeyboardInterrupt:
            print("\n[demo] Interrupción recibida.")
        finally:
            stop_nodes(nodes)

    else:
        # Modo normal: todos los nodos con el mismo protocolo
        nodes = build_nodes(
            names,
            topo,
            args.algo,
            transport=args.transport,   # <-- pasa transporte
            redis_cfg=redis_cfg,        # <-- pasa redis_cfg (o None)
        )

        signal.signal(signal.SIGINT, handle_sigint(nodes))

        try:
            start_nodes(nodes)

            if args.algo in ["lsr", "dvr"]:
                print(f"[demo] Warmup {args.warmup:.1f}s para {args.algo.upper()} (emisión/recepción de mensajes de control + computación de rutas)…")
            else:
                print(f"[demo] Warmup {args.warmup:.1f}s (HELLO/vecinos)…")
            time.sleep(max(0.0, args.warmup))

            if args.algo in ["lsr", "dvr"]:
                print_tables(nodes)

            demo_send(args.algo, nodes, args.src, args.dst, args.text, args.ttl)

            if args.after > 0:
                time.sleep(args.after)

        finally:
            stop_nodes(nodes)


if __name__ == "__main__":
    main()

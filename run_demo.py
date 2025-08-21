from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from router.node import Node
import argparse
import json
import signal
import time
from typing import Dict, List


# ---------- Utilidades para cargar archivos ----------
def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: no existe el archivo: {p}")
        sys.exit(2)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_names(path: str) -> Dict[str, Dict[str, int]]:
    data = load_json(path)
    names: Dict[str, Dict[str, int]] = {}
    for k, v in data.items():
        names[k] = {"host": v["host"], "port": int(v["port"])}
    return names


def load_topo(path: str) -> Dict[str, List[str]]:
    data = load_json(path)
    topo: Dict[str, List[str]] = {}
    for k, v in data.items():
        topo[k] = list(v)
    return topo


# ---------- Construcción / control de nodos ----------
def build_nodes(names: Dict[str, Dict[str, int]], topo: Dict[str, List[str]]) -> Dict[str, Node]:
    nodes: Dict[str, Node] = {}
    # Asegura que todos los que están en names existan en topo (aunque sea con lista vacía)
    for n in names.keys():
        topo.setdefault(n, [])

    for name, cfg in names.items():
        host = cfg["host"]
        port = int(cfg["port"])
        neighbors = topo.get(name, [])
        nodes[name] = Node(
            name=name,
            bind_host=host,
            bind_port=port,
            names=names,
            neighbors=neighbors,
        )
    return nodes


def start_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.start()
    print(f"[demo] {len(nodes)} nodos iniciados.")


def stop_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.stop()
    print("[demo] Nodos detenidos.")


def print_tables(nodes: Dict[str, Node]) -> None:
    print("\n[demo] Tablas de ruteo actuales (LSR):")
    for name in sorted(nodes.keys()):
        rt = getattr(nodes[name], "routing_table", {})
        print(f"  - {name}: {rt}")


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
        stop_nodes(nodes)
        sys.exit(0)

    return _handler


# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Run demo Lab 3 - Parte 1 (sockets locales)")
    parser.add_argument("--names", default="names-sample.json", help="Ruta a archivo JSON de nombres (host/port)")
    parser.add_argument("--topo", default="topo-sample.json", help="Ruta a archivo JSON de topología")
    parser.add_argument("--algo", choices=["lsr", "flooding"], default="lsr", help="Algoritmo para DATA")
    parser.add_argument("--src", default="A", help="Nodo origen lógico (clave en names)")
    parser.add_argument("--dst", default="D", help="Nodo destino lógico (clave en names)")
    parser.add_argument("--text", default="Hola desde demo", help="Texto a enviar")
    parser.add_argument("--ttl", type=int, default=10, help="TTL del mensaje DATA")
    parser.add_argument("--warmup", type=float, default=3.0, help="Segundos de espera antes de enviar DATA")
    parser.add_argument("--after", type=float, default=3.0, help="Segundos de espera tras enviar DATA")
    args = parser.parse_args()

    names = load_names(args.names)
    topo = load_topo(args.topo)
    nodes = build_nodes(names, topo)

    # Manejo de Ctrl+C
    signal.signal(signal.SIGINT, handle_sigint(nodes))

    try:
        start_nodes(nodes)

        if args.algo == "lsr":
            print(f"[demo] Warmup {args.warmup:.1f}s para LSR (emisión/recepción de LSP + Dijkstra)…")
        else:
            print(f"[demo] Warmup {args.warmup:.1f}s (HELLO/vecinos)…")
        time.sleep(max(0.0, args.warmup))

        if args.algo == "lsr":
            print_tables(nodes)

        demo_send(args.algo, nodes, args.src, args.dst, args.text, args.ttl)

        # Espera para que el tráfico llegue y se impriman entregas
        if args.after > 0:
            time.sleep(args.after)

    finally:
        stop_nodes(nodes)


if __name__ == "__main__":
    main()

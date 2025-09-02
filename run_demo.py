#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

# ========= Bootstrap imports (soporta tu estructura de carpetas) =========
import sys
import json
import time
import signal
import argparse
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# router/
from router.node import Node
from router.metrics import collector

# ========= Pretty console opcional =========
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except Exception:
    console = None
    Table = None

def _print(msg: str) -> None:
    if console:
        console.print(msg)
    else:
        print(msg)

# ========= Carga de archivos =========
def _unwrap_config(obj: dict) -> dict:
    """
    Acepta tanto { "A": {...} } como { "type": "...", "config": {...} }
    """
    if isinstance(obj, dict) and isinstance(obj.get("config"), dict):
        return obj["config"]
    return obj

def load_names(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No existe names JSON: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _unwrap_config(data)

def load_topo(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No existe topo JSON: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _unwrap_config(data)

# ========= Helpers de UI =========
def cli_show_routes(nodes: Dict[str, Node], src: str) -> None:
    n = nodes[src]
    rt = getattr(n, "routing_table", {}) or {}
    proto = getattr(n, "routing_protocol", "unknown").upper()

    if console and Table:
        t = Table(title=f"🗺️  Tabla de Enrutamiento de {src}  ({proto})",
                  header_style="bold cyan")
        t.add_column("Destino", justify="center", style="magenta")
        t.add_column("Next-Hop", justify="center", style="green")
        t.add_column("Costo", justify="center", style="yellow")
        for dest, nh in sorted(rt.items()):
            cost = "—"
            try:
                c = n.graph.get(src, {}).get(nh)
                if c is not None:
                    cost = f"{float(c):.1f}"
            except Exception:
                pass
            t.add_row(dest, nh, str(cost))
        if not rt:
            t.add_row("—", "—", "—")
        console.print(t)
    else:
        print(f"Tabla de Enrutamiento de {src} ({proto})")
        if not rt:
            print("  (vacía)")
        for dest, nh in sorted(rt.items()):
            cost = n.graph.get(src, {}).get(nh, "—")
            print(f"  {dest:>3} -> {nh:>3}  costo={cost}")

def cli_show_lsdb(nodes: Dict[str, Node], src: str) -> None:
    n = nodes[src]
    lsdb = getattr(n, "graph", {}) or {}

    if console and Table:
        t = Table(title=f"📚  LSDB de {src}", header_style="bold magenta")
        t.add_column("Nodo", style="cyan", justify="center")
        t.add_column("Vecino", style="green", justify="center")
        t.add_column("Costo", style="yellow", justify="center")

        if not lsdb:
            t.add_row("—", "—", "—")
        else:
            for u, nbrs in sorted(lsdb.items()):
                if not nbrs:
                    t.add_row(u, "—", "—")
                else:
                    for v, w in sorted(nbrs.items()):
                        try:
                            w = float(w)
                        except Exception:
                            pass
                        t.add_row(u, v, f"{w}")
        console.print(t)
    else:
        print(f"LSDB de {src}")
        if not lsdb:
            print("  (vacía)")
        else:
            for u, nbrs in sorted(lsdb.items()):
                if not nbrs:
                    print(f"  {u}: (sin vecinos)")
                else:
                    for v, w in sorted(nbrs.items()):
                        print(f"  {u} -> {v}   costo={w}")

# ========= Construcción de nodos =========
def build_nodes(
    names: dict,
    topo: dict,
    algo: str,
    transport: str = "udp",
    redis_cfg: dict | None = None,
) -> Dict[str, Node]:

    allowed = {"lsr", "dvr", "flooding"}
    proto_default = (algo or "lsr").lower()
    if proto_default not in allowed:
        proto_default = "lsr"

    # Garantiza que todos existan en el topo
    for n in names.keys():
        topo.setdefault(n, [])

    # Map de canales si usas Redis
    channel_map = {}
    if transport == "redis":
        for k, v in names.items():
            ch = v.get("channel")
            if not ch:
                raise ValueError(f"[Redis] Falta 'channel' para '{k}' en names-redis.json")
            channel_map[k] = str(ch)

    nodes: Dict[str, Node] = {}

    for name, cfg in names.items():
        if transport == "udp":
            host = cfg["host"]
            port = int(cfg["port"])
        else:
            host = "127.0.0.1"   # no se usa en Redis
            port = 0

        neighbors = list(topo.get(name, []))
        node_proto = str(cfg.get("protocol", proto_default)).lower()
        if node_proto not in allowed:
            node_proto = proto_default

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
            routing_protocol=node_proto,
        )

    return nodes

# ========= Arranque/paro + métricas =========
def start_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.start()
    _print(f"[demo] {list(nodes.keys())} nodos iniciados.")

def stop_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.stop()
    _print("[demo] Nodos detenidos.")

def handle_sigint(nodes: Dict[str, Node]):
    def _handler(_sig, _frm):
        print("\n[demo] SIGINT recibido. Deteniendo nodos…")
        try:
            collector.print_summary()
            ts = int(time.time())
            report = collector.get_comparison_report()
            outfile = f"demo_metrics_{ts}.json"
            with open(outfile, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"[demo] 💾 Métricas guardadas: {outfile}")
        except Exception as e:
            print(f"[demo] Sin métricas: {e}")
        stop_nodes(nodes)
        sys.exit(0)
    return _handler

# ========= Envíos demo =========
def demo_send(algo: str, nodes: Dict[str, Node], src: str, dst: str, text: str, ttl: int) -> None:
    if src not in nodes or dst not in nodes:
        print(f"ERROR: src/dst inválidos. src={src} dst={dst}")
        return
    n = nodes[src]
    if algo == "flooding":
        n.send_data_flood(dst, text, max_hops=ttl)
    else:
        n.send_data(dst, text)

def demo_broadcast(nodes: Dict[str, Node], src: str, text: str, ttl: int = 10) -> None:
    n = nodes[src]
    for v in list(n.neighbors):
        _print(f"[demo] 📡 Broadcast inicial → {v}")
        n.send_data_flood(v, text, max_hops=ttl)

# ========= CLI interactivo =========
# ========= CLI interactivo =========
def handle_cli_command(nodes: Dict[str, Node], src: str, cmd: str) -> None:
    cmd = (cmd or "").strip()
    if not cmd:
        return
    parts = cmd.split(maxsplit=2)
    op = parts[0].lower()

    if op == "nodes":
        n = nodes[src]
        if console and Table:
            t = Table(title="🌐 NODOS DISPONIBLES", header_style="bold magenta")
            t.add_column("Nodo", style="cyan", justify="center")
            t.add_column("Estado", style="green", justify="center")
            t.add_column("Canal", style="yellow")
            for k, cfg in n.names.items():
                canal = cfg.get("channel", "")
                estado = "🟢 (YO)" if k == src else ("🟢 Activo" if n.neighbor_up.get(k, False) else "⚪ Inactivo")
                t.add_row(k, estado, canal)
            console.print(t)
        else:
            for k, cfg in nodes[src].names.items():
                canal = cfg.get("channel", "")
                print(f"{k:>3} -> {canal}")
        return

    # sendf <DST> <texto...>  (flood unicast)
    if op in ("sendf", "flood") and len(parts) >= 3:
        dst = parts[1]
        text = parts[2]
        nodes[src].send_data_flood(dst, text, ttl=12)
        return

    # broadcast <texto...> (flood a todos, una inyección por vecino)
    if op == "broadcast" and len(parts) >= 2:
        text = parts[1] if len(parts) == 2 else parts[1] + " " + parts[2]
        for v in list(nodes[src].neighbors):
            nodes[src].send_data_flood(v, text, ttl=12)
        return

    # ping <dst>   (HELLO/ECHO)
    if op == "ping" and len(parts) >= 2:
        dst = parts[1]
        nodes[src].send_hello(dst)
        time.sleep(0.7)
        rtt = nodes[src].neighbor_rtt_ms.get(dst)
        if rtt is not None:
            print(f"[{src}] RTT {src}↔{dst}: {rtt:.1f} ms")
        return

    # info [vecino]  (LSP real)
    if op == "info":
        n = nodes[src]
        if len(parts) >= 2 and parts[1]:
            n._emit_info_lsr(only_to=parts[1])
        else:
            n._emit_info_lsr()
        print(f"[{src}] INFO (LSR) emitido.")
        return

    # show routes | show lsdb
    if op == "show" and len(parts) >= 2:
        what = parts[1].lower()
        if what == "routes":
            cli_show_routes(nodes, src)
            return
        if what == "lsdb":
            cli_show_lsdb(nodes, src)
            return

    if op in ("q", "quit", "exit"):
        return

    print(f"Comando no reconocido: {cmd}")

# ========= Main =========
def main():
    ap = argparse.ArgumentParser(description="Demo Lab 3 — Routing")
    ap.add_argument("--names", default="names-sample.json")
    ap.add_argument("--topo",  default="topo-sample.json")
    ap.add_argument("--algo", choices=["lsr", "dvr", "flooding"], default="lsr")
    ap.add_argument("--transport", choices=["udp", "redis"], default="udp")
    ap.add_argument("--src", default="A")
    ap.add_argument("--dst", default="B")
    ap.add_argument("--text", default="Hola desde demo")
    ap.add_argument("--ttl", type=int, default=10)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--shell", action="store_true")
    ap.add_argument("--after-cmd", default="")
    ap.add_argument("--ping", action="store_true")

    # Redis
    ap.add_argument("--redis-host", default="lab3.redesuvg.cloud")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-username", default="default")
    ap.add_argument("--redis-password", default="UVGRedis2025")

    args = ap.parse_args()

    names = load_names(args.names)
    topo  = load_topo(args.topo)

    redis_cfg = None
    if args.transport == "redis":
        redis_cfg = {
            "host": args.redis_host,
            "port": args.redis_port,
            "username": args.redis_username,
            "password": args.redis_password,
            "decode_responses": True,
        }

    nodes = build_nodes(
        names=names,
        topo=topo,
        algo=args.algo,
        transport=args.transport,
        redis_cfg=redis_cfg,
    )

    signal.signal(signal.SIGINT, handle_sigint(nodes))

    try:
        start_nodes(nodes)

        if args.algo in ("lsr", "dvr"):
            _print(f"[demo] Warmup {args.warmup:.1f}s para {args.algo.upper()} "
                   "(mensajes de control + cómputo de rutas)…")
        else:
            _print("[demo] Warmup 3.0s (HELLO/vecinos)…")

        time.sleep(max(0.0, args.warmup))

        # Mostrar estado de la fuente
        if args.algo == "lsr":
            _print("[demo] Mostrando LSDB y rutas (LSR)…")
            cli_show_lsdb(nodes, args.src)
            cli_show_routes(nodes, args.src)

        # Ping one-shot por bandera
        if args.ping:
            print(f"[demo] PING {args.src} → {args.dst} (HELLO/ECHO)…")
            nodes[args.src].send_hello(args.dst)
            time.sleep(1.0)
            rtt = nodes[args.src].neighbor_rtt_ms.get(args.dst)
            if rtt is not None:
                print(f"[demo] RTT {args.src}↔{args.dst}: {rtt:.1f} ms")
            else:
                print(f"[demo] RTT {args.src}↔{args.dst}: (sin medición)")

        # Tablas rápidas
        print("\n[demo] Tablas de ruteo actuales:")
        for k in sorted(nodes.keys()):
            proto = getattr(nodes[k], "routing_protocol", "unknown").upper()
            rt = getattr(nodes[k], "routing_table", {})
            print(f"  - {k} ({proto}): {rt}")

        # after-cmd o shell
        if args.after_cmd:
            handle_cli_command(nodes, args.src, args.after_cmd)
        elif args.shell:
            try:
                while True:
                    cmd = input(f"[{args.src}]> ").strip()
                    if cmd.lower() in ("q", "quit", "exit"):
                        break
                    handle_cli_command(nodes, args.src, cmd)
            finally:
                stop_nodes(nodes)
                return
        else:
            # Demo simple: enviar mensaje una vez
            demo_send(args.algo, nodes, args.src, args.dst, args.text, args.ttl)

    finally:
        stop_nodes(nodes)

if __name__ == "__main__":
    main()

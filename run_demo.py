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


try:
    from rich.console import Console
    console = Console()
    def dprint(msg): console.print(msg)
except Exception:
    def dprint(msg): print(msg)
# --- Emojis para logs vistosos ---
ICON_OK = "🟢"
ICON_NODE = "🟢"
ICON_NEUTRAL = "⚪"
ICON_SEND = "🚀"
ICON_BROADCAST = "📡"
ICON_WARN = "⚠️"
ICON_ERR = "❌"



# --- pretty console helpers ---
try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
except Exception:
    _console = None
    Table = None

def _print(msg: str):
    if _console:
        _console.print(msg)
    else:
        print(msg)

def cli_show_routes(nodes, src: str):
    """Muestra la tabla de ruteo del nodo `src` (LSR/Dijkstra)."""
    n = nodes[src]
    rt = getattr(n, "routing_table", {}) or {}
    proto = getattr(n, "routing_protocol", "unknown").upper()

    if _console and Table:
        table = Table(title=f"🗺️  Tabla de Enrutamiento de {src}  ({proto})",
                      header_style="bold cyan")
        table.add_column("Destino", justify="center", style="magenta")
        table.add_column("Next-Hop", justify="center", style="green")
        table.add_column("Costo (enlace directo)", justify="center", style="yellow")

        for dest, nh in sorted(rt.items()):
            # Para costo mostramos el costo del primer enlace (src->nh) si existe
            cost = "—"
            try:
                cost_val = n.graph.get(src, {}).get(nh)
                if cost_val is not None:
                    cost = f"{float(cost_val):.1f}"
            except Exception:
                pass
            table.add_row(dest, nh, str(cost))

        if not rt:
            table.add_row("—", "—", "—")

        _console.print(table)
    else:
        print(f"Tabla de Enrutamiento de {src} ({proto})")
        if not rt:
            print("  (vacía)")
        for dest, nh in sorted(rt.items()):
            cost_val = n.graph.get(src, {}).get(nh, "—")
            print(f"  {dest:>3}  ->  {nh:>3}   costo={cost_val}")

def cli_show_lsdb(nodes, src: str):
    """Muestra la 'LSDB' vista por `src`: n.graph = {u:{v:costo}}."""
    n = nodes[src]
    lsdb = getattr(n, "graph", {}) or {}

    if _console and Table:
        table = Table(title=f"📚  LSDB vista por {src}", header_style="bold magenta")
        table.add_column("Nodo", style="cyan", justify="center")
        table.add_column("Vecino", style="green", justify="center")
        table.add_column("Costo", style="yellow", justify="center")

        if not lsdb:
            table.add_row("—", "—", "—")
        else:
            for u, nbrs in sorted(lsdb.items()):
                if not nbrs:
                    table.add_row(u, "—", "—")
                else:
                    for v, w in sorted(nbrs.items()):
                        try:
                            w = float(w)
                        except Exception:
                            pass
                        table.add_row(u, v, f"{w}")
        _console.print(table)
    else:
        print(f"LSDB vista por {src}")
        if not lsdb:
            print("  (vacía)")
        else:
            for u, nbrs in sorted(lsdb.items()):
                if not nbrs:
                    print(f"  {u}: (sin vecinos)")
                else:
                    for v, w in sorted(nbrs.items()):
                        print(f"  {u} -> {v}   costo={w}")


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


import json


def cli_show_routes(nodes, src):
    from rich.console import Console
    from rich.table import Table

    n = nodes[src]
    rt = getattr(n, "routing_table", {}) or {}
    proto = getattr(n, "routing_protocol", "unknown").upper()

    console = Console()
    table = Table(title=f"🗺️  Tabla de Enrutamiento de {src}  ({proto})",
                  header_style="bold cyan")
    table.add_column("Destino", justify="center", style="magenta")
    table.add_column("Next-Hop", justify="center", style="green")
    table.add_column("Costo", justify="center", style="yellow")

    # costo = 1 si hay enlace directo en n.graph; si no, deja “—”
    for dest, nh in sorted(rt.items()):
        cost = "—"
        try:
            cost_val = n.graph.get(src, {}).get(nh)
            if cost_val is not None:
                cost = f"{cost_val:.1f}"
        except Exception:
            pass
        table.add_row(dest, nh, str(cost))

    if not rt:
        table.add_row("—", "—", "—")

    console.print(table)

def cli_show_lsdb(nodes, src):
    from rich.console import Console
    from rich.table import Table

    n = nodes[src]
    # Usamos n.graph como “LSDB” simple: {node: {vecino: costo}}
    lsdb = getattr(n, "graph", {}) or {}

    console = Console()
    table = Table(title=f"📚  LSDB de {src}", header_style="bold magenta")
    table.add_column("Nodo", style="cyan", justify="center")
    table.add_column("Vecino", style="green", justify="center")
    table.add_column("Costo", style="yellow", justify="center")

    if not lsdb:
        table.add_row("—", "—", "—")
    else:
        for u, nbrs in sorted(lsdb.items()):
            if not nbrs:
                table.add_row(u, "—", "—")
            else:
                for v, w in sorted(nbrs.items()):
                    table.add_row(u, v, f"{float(w):.1f}")

    console.print(table)


def load_names(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Acepta los dos formatos:
    # 1) plano: {"A": {...}, "B": {...}}
    # 2) envuelto: {"type": "names", "config": {...}}
    if isinstance(data, dict) and "config" in data and isinstance(data["config"], dict):
        return data["config"]
    return data

def load_topo(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Acepta:
    # 1) plano: {"A": ["B"], "B": ["A"]}
    # 2) envuelto: {"type": "topo", "config": {...}}
    if isinstance(data, dict) and "config" in data and isinstance(data["config"], dict):
        return data["config"]
    return data



# ---------- Construcción / control de nodos ----------
def build_nodes(names, topo, algo, transport="udp", redis_cfg=None):
    nodes = {}

    # Protocolos permitidos
    allowed = {"lsr", "dvr", "flooding"}
    default_protocol = str(algo).lower()
    if default_protocol not in allowed:
        default_protocol = "lsr"

    # Asegurar que todos los nodos de 'names' existan en 'topo'
    for n in names.keys():
        topo.setdefault(n, [])

    # Mapa lógico -> canal Redis
    if transport == "redis":
        channel_map = {}
        for k, v in names.items():
            ch = v.get("channel")
            if not ch:
                raise ValueError(f"[Redis] Falta 'channel' para el nodo '{k}' en names-redis.json")
            channel_map[k] = str(ch)
    else:
        # En UDP no usamos canales; dejamos identidad 1:1
        channel_map = {k: k for k in names.keys()}

    # Construcción de nodos
    for name, cfg in names.items():
        if transport == "udp":
            host = cfg["host"]
            port = cfg["port"]
        else:  # transport == "redis"
            host = "127.0.0.1"   # dummy, no se usa
            port = 0             # dummy

        neighbors = topo.get(name, [])

        # Protocolo por nodo (override opcional en names.json)
        node_protocol = str(cfg.get("protocol", default_protocol)).lower()
        if node_protocol not in allowed:
            node_protocol = default_protocol

        # redis_cfg específico del nodo
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
    dprint(f"[bold white on black][demo][/bold white on black] {ICON_SEND} Enviando [bold]MESSAGE {algo.upper()}[/bold] "
       f"[magenta]{src}[/magenta] → [cyan]{dst}[/cyan]: [yellow]'{text}'[/yellow] (ttl={ttl})")

    if algo == "flooding":
        node_src.send_data_flood(dst, text, ttl=ttl)
    else:
        # LSR por defecto
        node_src.send_data(dst, text, ttl=ttl)


def demo_broadcast(nodes: Dict[str, Node], src: str, text: str, ttl: int = 10) -> None:
    """
    Emula 'broadcast': desde 'src' envía por flooding un MESSAGE por cada vecino destino lógico.
    En node.py el flooding reenvía mientras m.dst != self.name, así que la red completa lo verá.
    """
    if src not in nodes:
        print(f"ERROR: src inválido: {src}")
        return
    n = nodes[src]
    for v in list(n.neighbors):
        dprint(f"[bold white on black][demo][/bold white on black] {ICON_BROADCAST} Broadcast inicial a [green]{v}[/green]")
        n.send_data_flood(v, text, ttl=ttl)

def handle_cli_command(nodes: Dict[str, Node], src: str, cmd: str) -> None:
    """
    Intérprete de comandos simples:
      - nodes
      - broadcast <texto>
      - send <DST> <texto>
      - ping <DST>
    """
    cmd = cmd.strip()
    if not cmd:
        return

    parts = cmd.split(maxsplit=2)
    op = parts[0].lower()

    if op == "nodes":
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="🌐 NODOS DISPONIBLES", header_style="bold magenta")

        table.add_column("Nodo", style="cyan", justify="center")
        table.add_column("Estado", style="green", justify="center")
        table.add_column("Canal", style="yellow")

        for k, cfg in nodes[src].names.items():
            canal = cfg.get("channel", "")
            if k == src:
                estado = "🟢 (YO)"
            else:
                estado = "⚪ Activo"
            table.add_row(k, estado, canal)

        console.print(table)
        return


    if op == "broadcast" and len(parts) >= 2:
        text = parts[1] if len(parts) == 2 else parts[1] + " " + parts[2]
        demo_broadcast(nodes, src, text, ttl=10)
        return
    
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

    if op == "send" and len(parts) >= 3:
        dst, text = parts[1], parts[2]
        demo_send("flooding", nodes, src, dst, text, ttl=10)
        return

    if op == "ping" and len(parts) >= 2:
        dst = parts[1]
        nodes[src].send_hello(dst)
        return
    
    if op == "show" and len(parts) >= 2:
        what = parts[1].lower()
        if what == "routes":
            cli_show_routes(nodes, src)
            return
        if what == "lsdb":
            cli_show_lsdb(nodes, src)
            return
    
    if op == "show" and len(parts) >= 2:
        what = parts[1].lower()
        if what == "dv":
            n = nodes[src]
            try:
                from router.node import print_dv_table  # o import al inicio
            except Exception:
                pass
            print_dv_table(n)
            return



    # (opcional) 'show routes all'
    if op == "show" and len(parts) >= 3 and parts[1].lower() == "routes" and parts[2].lower() == "all":
        for k in sorted(nodes.keys()):
            cli_show_routes(nodes, k)
        return

    print(f"Comando no reconocido: {cmd}")

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
    # antes: print(f"[demo] {n} nodos iniciados.")
    dprint(f"[bold white on black][demo][/bold white on black] [green]{n}[/green] nodos iniciados.")
        

def stop_nodes(nodes: Dict[str, Node]) -> None:
    for n in nodes.values():
        n.stop()
    dprint("[bold white on black][demo][/bold white on black] [green]Nodos detenidos.[/green]")

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
    parser.add_argument("--no-demo", action="store_true", help="No enviar demo automática después del warmup")
    parser.add_argument("--shell", action="store_true",
                    help="Entrar a modo interactivo (prompt) tras el warmup")
    parser.add_argument("--after-cmd", default="",
                        help="Comando CLI para ejecutar tras el warmup (p.ej. 'broadcast HOLA')")

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
    parser.add_argument("--redis-username", default="default")
    parser.add_argument("--ping", action="store_true",
                    help="Enviar HELLO/echo del --src a --dst y mostrar RTT")


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

    if args.algo == "dvr":
        try:
            from router.node import print_dv_table
            print_dv_table(nodes[args.src])
        except Exception:
            pass

    if args.transport == "redis":
        redis_cfg = {
            "host": args.redis_host,
            "port": args.redis_port,
            "password": args.redis_password,
            "username": args.redis_username,
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
            print("[demo] Enviando MESSAGE A→D usando LSR...")
            nodes["A"].send_data("D", "Hola desde A usando LSR", ttl=10)

            print("[demo] Enviando MESSAGE C→A usando DVR...")
            nodes["C"].send_data("A", "Hola desde C usando DVR", ttl=10)

            print("[demo] Enviando MESSAGE B→C usando Flooding...")
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
                dprint("[bold white on black][demo][/bold white on black] Warmup 3.0s [yellow](HELLO/vecinos)[/yellow]…")
            time.sleep(max(0.0, args.warmup))
            # en run_demo.py, después del warmup:

            if args.algo == "lsr":
                _print("[bold white on black][demo][/bold white on black] Mostrando LSDB y rutas (LSR)…")
                # Para el nodo fuente:
                try:
                    cli_show_lsdb(nodes, args.src)
                    cli_show_routes(nodes, args.src)
                except Exception:
                    pass


            # <<< AQUI VA EL PING >>>
            if args.ping:
                print(f"[demo] PING {args.src} → {args.dst} (HELLO/echo)…")
                nodes[args.src].send_hello(args.dst)
                time.sleep(1.0)  # da tiempo a que regrese el echo
                rtt = nodes[args.src].neighbor_rtt_ms.get(args.dst)
                if rtt is not None:
                    print(f"[demo] RTT {args.src}↔{args.dst}: {rtt:.2f} ms")
                else:
                    print(f"[demo] RTT {args.src}↔{args.dst}: (sin medición)")

            if args.algo in ["lsr", "dvr"]:
                print_tables(nodes)

                        # --- flujo tras warmup ---
            # a) si pasaron un comando, ejecútalo (ej. --after-cmd "broadcast HOLA")
            if args.after_cmd:
                handle_cli_command(nodes, args.src, args.after_cmd)

            # b) si pidieron shell, entra a modo interactivo
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

            # c) si no, ejecuta la demo automática (una sola vez) y termina
            else:
                demo_send(args.algo, nodes, args.src, args.dst, args.text, args.ttl)



        finally:
            stop_nodes(nodes)



if __name__ == "__main__":
    main()

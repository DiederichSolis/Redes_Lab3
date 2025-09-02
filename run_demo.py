# run_demo.py  — shell con colores, emojis y tablas
import json
import argparse
import time
from typing import Dict, Set
from router.node import Node
from router.message import Message


# ========== Colores/estilos ==========
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    FG_RED = "\033[91m"
    FG_GREEN = "\033[92m"
    FG_YELLOW = "\033[93m"
    FG_BLUE = "\033[94m"
    FG_MAGENTA = "\033[95m"
    FG_CYAN = "\033[96m"
    FG_GRAY = "\033[90m"
    FG_WHITE = "\033[97m"


def load_names(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    names: Dict[str, str] = {}
    for node, cfg in data.items():
        if isinstance(cfg, dict):
            names[node] = cfg.get("channel") or node
        else:
            names[node] = str(cfg)
    return names


def load_topo(path: str) -> Dict[str, Set[str]]:
    with open(path, "r", encoding="utf-8") as f:
        topo = json.load(f)
    G: Dict[str, Set[str]] = {}
    for u, nbrs in topo.items():
        G.setdefault(u, set())
        for v in nbrs:
            G.setdefault(v, set())
            G[u].add(v)
            G[v].add(u)
    return G


# ========== helpers de impresión bonitos ==========
def title(text: str):
    print(f"{C.BOLD}{C.FG_CYAN}{text}{C.RESET}")

def subtitle(text: str):
    print(f"{C.BOLD}{C.FG_MAGENTA}{text}{C.RESET}")

def label(text: str):
    return f"{C.DIM}{text}{C.RESET}"

def ok(text: str):
    print(f"{C.FG_GREEN}✔ {text}{C.RESET}")

def warn(text: str):
    print(f"{C.FG_YELLOW}⚠ {text}{C.RESET}")

def err(text: str):
    print(f"{C.FG_RED}✖ {text}{C.RESET}")


def print_routes_table(node: Node):
    routes = node.route_table
    title(f"🛣️  Rutas ({node.algorithm.upper()})")
    if not routes:
        warn("no hay rutas (aún)")
        return
    print(f"{label('dest'):12s} {label('→ next hop')}")
    for dst, nh in sorted(routes.items()):
        print(f"{C.BOLD}{dst:<12s}{C.RESET} → {C.FG_GREEN}{nh}{C.RESET}")


def print_neighbors(node: Node):
    title("👥 Vecinos directos")
    if not node.neighbor_ids:
        warn("sin vecinos")
        return
    print(f"{label('vecino'):12s} {label('estado'):10s} {label('último_echo(ms)')}")
    now = time.time()
    for v in sorted(node.neighbor_ids):
        last = node.neigh_last_seen.get(v, 0.0)
        up = (now - last) <= 6.0
        rtt = node.neighbor_rtt_ms.get(v)
        st = f"{C.FG_GREEN}🟢 UP{C.RESET}" if up else f"{C.FG_RED}🔴 DOWN{C.RESET}"
        rtt_str = f"{rtt:.1f}" if rtt is not None else "-"
        print(f"{C.BOLD}{v:<12s}{C.RESET} {st:<10s} {rtt_str:>10s}")


def print_lsdb(node: Node):
    title("🗺️  Link-State DB (LSR)")
    lsdb = node.get_lsdb()
    if not lsdb:
        warn("LSDB vacía")
        return
    print(f"{label('u'):12s} {label('→ v'):12s} {label('costo')}")
    rows = []
    for u in sorted(lsdb.keys()):
        if not lsdb[u]:
            rows.append((u, "(sin vecinos)", ""))
        else:
            for v in sorted(lsdb[u].keys()):
                rows.append((u, v, f"{lsdb[u][v]}"))
    for u, v, c in rows:
        print(f"{C.BOLD}{u:<12s}{C.RESET} → {C.FG_BLUE}{v:<12s}{C.RESET} {c}")


def print_dv(node: Node):
    title("🧭 Distance Vector (DVR)")
    if not hasattr(node, "dvr"):
        warn("DVR no inicializado")
        return
    table = node.dvr.get_distance_table()
    if not table:
        warn("DV vacío")
        return
    print(f"{label('dest'):12s} {label('dist'):10s} {label('next_hop')}")
    for d in sorted(table.keys()):
        dist, nh = table[d]
        dist_str = "∞" if dist >= 1e12 else f"{dist:.3f}"
        nh_str = nh if nh is not None else "—"
        print(f"{C.BOLD}{d:<12s}{C.RESET} {dist_str:>10s} {C.FG_GREEN}{nh_str}{C.RESET}")


def print_nodes(node: Node):
    title("🧩 Nodos conocidos")
    for k in sorted(node.channel_by_node.keys()):
        ch = node.channel_by_node[k]
        badge = f"{C.FG_GREEN}🟢 vecino{C.RESET}" if k in node.neighbor_ids else f"{C.FG_GRAY}—{C.RESET}"
        print(f"{C.BOLD}{k:<12s}{C.RESET} {label('canal')}={ch}  {badge}")


def shell_help(node: Node):
    subtitle("Comandos")
    print(f"{C.BOLD}send <dst> <texto>{C.RESET}      {label('→ unicast respetando alg')}")
    # ✅ corregido (concatena para no pelear con comillas)
    print(f"{C.BOLD}broadcast <texto>{C.RESET}       " + label("→ to='*' por flooding"))
    print(f"{C.BOLD}ping <vecino>{C.RESET}           {label('→ HELLO para medir RTT')}")
    print(f"{C.BOLD}info [vecino]{C.RESET}           {label('→ LSR: LSP; DVR: vector dist (a todos o a uno)')}")
    print(f"{C.BOLD}show routes{C.RESET}             {label('→ tabla de ruteo (LSR/DVR)')}")
    print(f"{C.BOLD}show neighbors{C.RESET}          {label('→ vecinos directos')}")
    print(f"{C.BOLD}show lsdb{C.RESET}               {label('→ Link-State DB [LSR]')}")
    print(f"{C.BOLD}show dv{C.RESET}                 {label('→ Distance Vector [DVR]')}")
    print(f"{C.BOLD}nodes{C.RESET}                   {label('→ nodos conocidos y si son vecinos')}")
    print(f"{C.BOLD}exit / quit{C.RESET}             {label('→ salir')}")



def shell_loop(node: Node):
    print(f"{C.FG_CYAN}{C.BOLD}Entraste al shell del nodo {node.name}{C.RESET} {label(f'(alg={node.algorithm})')}")
    shell_help(node)
    while True:
        try:
            line = input(f"{C.FG_CYAN}{node.name}› {C.RESET}").strip()
        except EOFError:
            break
        if not line:
            continue
        low = line.lower()
        if low in {"quit", "exit"}:
            ok("saliendo…")
            break

        if line.startswith("send "):
            try:
                _, dst, text = line.split(" ", 2)
            except ValueError:
                err("Uso: send <dst> <texto>")
                continue
            node.send_data(dst_node=dst, payload=text, hops=16, alg=node.algorithm)
            ok(f"📬 enviado a {dst}")

        elif line.startswith("broadcast "):
            text = line[len("broadcast "):].strip()
            if not text:
                err("Uso: broadcast <texto>")
                continue
            node.send_data(dst_node="*", payload=text, hops=16, alg="flooding")
            ok("📣 broadcast enviado")

        elif line.startswith("ping "):
            dst = line.split(" ", 1)[1].strip()
            if not dst:
                err("Uso: ping <vecino>")
                continue
            node.send_hello(dst)
            time.sleep(0.6)
            rtt = node.neighbor_rtt_ms.get(dst)
            if rtt is not None:
                ok(f"📡 RTT {node.name}↔{dst}: {rtt:.1f} ms")
            else:
                warn("RTT no disponible (¿llegó el ECHO?)")

        elif line.startswith("info"):
            parts = line.split()
            only = parts[1] if len(parts) >= 2 else None
            if node.algorithm == "lsr":
                node._emit_info_lsr(only_to=only)
                ok(f"🗺️ INFO/LSR emitido{' a '+only if only else ' a todos'}")
            elif node.algorithm == "dvr":
                if only:
                    vec = node.dvr.announce_for(only)
                    node.dvr_seq += 1
                    msg = Message.info_lsr(src=node.name, seq_num=node.dvr_seq, neighbors=vec, hops=16, alg="dvr")
                    node.send_to_node(only, msg)
                    ok(f"🧭 INFO/DVR emitido a {only}")
                else:
                    node._emit_info_dvr()
                    ok("🧭 INFO/DVR emitido a todos")
            else:
                warn("Flooding no usa INFO manual.")

        elif low == "show routes":
            print_routes_table(node)

        elif low == "show neighbors":
            print_neighbors(node)

        elif low == "show lsdb":
            print_lsdb(node)

        elif low == "show dv":
            print_dv(node)

        elif low == "nodes":
            print_nodes(node)

        elif low in {"help", "h", "?"}:
            shell_help(node)

        else:
            warn(f"comando desconocido: {line}")


def build_nodes(names: Dict[str, str], topo: Dict[str, Set[str]], alg="lsr",
                transport="redis", redis_cfg=None) -> Dict[str, Node]:
    nodes: Dict[str, Node] = {}
    for me in names.keys():
        neighbor_ids = sorted(list(topo.get(me, [])))
        node = Node(
            name=me,
            channel=names[me],
            neighbor_ids=neighbor_ids,
            channel_by_node=names,
            algorithm=alg,
            transport=transport,
            redis_cfg=redis_cfg or {}
        )
        node.start()
        nodes[me] = node
    return nodes


def stop_nodes(nodes: Dict[str, Node]):
    for n in nodes.values():
        n.stop()


def main():
    ap = argparse.ArgumentParser(description="Demo de nodos con Redis + LSR/DVR/Flooding")
    ap.add_argument("--names", default="names-redis.json", help="Ruta del JSON de names")
    ap.add_argument("--topo", default="topo-redis.json", help="Ruta del JSON de topología")
    ap.add_argument("--alg", choices=["lsr", "flooding", "dvr"], default="lsr")
    ap.add_argument("--transport", choices=["redis"], default="redis")

    # Redis
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-user", default=None)
    ap.add_argument("--redis-pass", default="")

    # Modo envío exprés (multinodo)
    ap.add_argument("--send", default="", help="Envía un mensaje: SRC:DST:Texto (ej. A:C:'Hola')")
    ap.add_argument("--run-seconds", type=int, default=40, help="Segundos a mantener la demo viva")

    # Modo shell por nodo
    ap.add_argument("--src", help="ID del nodo para shell (ej. B)")
    ap.add_argument("--shell", action="store_true", help="Inicia shell interactivo para --src")

    args = ap.parse_args()

    names = load_names(args.names)
    topo = load_topo(args.topo)

    redis_cfg = dict(host=args.redis_host, port=args.redis_port, password=args.redis_pass)
    if args.redis_user:
        redis_cfg["username"] = args.redis_user

    # --- SHELL (un nodo) ---
    if args.src and args.shell:
        if args.src not in names:
            err(f"--src '{args.src}' no existe en {args.names}")
            return
        node = Node(
            name=args.src,
            channel=names[args.src],
            neighbor_ids=sorted(list(topo.get(args.src, []))),
            channel_by_node=names,
            algorithm=args.alg,
            transport=args.transport,
            redis_cfg=redis_cfg,
        )
        node.start()
        try:
            time.sleep(2.5)
            shell_loop(node)
        finally:
            node.stop()
        return

    # --- MULTINODO (todos) ---
    nodes = build_nodes(names, topo, args.alg, args.transport, redis_cfg)

    try:
        time.sleep(4)
        if args.send:
            try:
                src, dst, text = args.send.split(":", 2)
                if src not in nodes:
                    err(f"SRC '{src}' no existe en names.")
                else:
                    ok(f"📬 enviando '{text}' de {src} a {dst} (alg={args.alg})")
                    nodes[src].send_data(dst_node=dst, payload=text, hops=16, alg=args.alg)
            except ValueError:
                err("--send debe ser 'SRC:DST:Texto' (usa comillas si hay espacios)")

        t0 = time.time()
        while time.time() - t0 < args.run_seconds:
            time.sleep(0.5)

    except KeyboardInterrupt:
        warn("interrumpido por usuario")
    finally:
        stop_nodes(nodes)


if __name__ == "__main__":
    main()

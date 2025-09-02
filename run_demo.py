# run_demo.py
import json
import argparse
import time
from typing import Dict, Set
from router.node import Node
from router.message import Message  # para enviar INFO dirigido en DVR


def load_names(path: str) -> Dict[str, str]:
    """
    JSON aceptado:
      extendido: {"A":{"channel":"sec10.group7.Joge"}, ...}
      compacto : {"A":"sec10.group7.Joge", ...}
    Retorna: {"A":"sec10.group7.Joge", ...}
    """
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
    """
    JSON esperado (no dirigido):
      {"A":["B","C"], "B":["A","C"], "C":["A","B"]}
    Retorna: dict[str, set[str]]
    """
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


def shell_loop(node: Node):
    print(f"Entraste al shell del nodo {node.name} (alg={node.algorithm}).")
    print("Comandos:")
    print("  send <dst> <texto>      -> unicast respetando alg")
    print("  broadcast <texto>       -> to='*' por flooding")
    print("  ping <vecino>           -> HELLO para medir RTT")
    print("  info [vecino]           -> LSR: LSP;  DVR: vector dist (a todos o a uno)")
    print("  show routes             -> tabla de ruteo (LSR/DVR)")
    print("  show neighbors          -> vecinos directos")
    print("  show lsdb               -> Link-State DB (grafo con costos)  [LSR]")
    print("  show dv                 -> Distance Vector local              [DVR]")
    print("  nodes                   -> nodos conocidos y si son vecinos")
    print("  exit / quit             -> salir")
    while True:
        try:
            line = input(f"{node.name}> ").strip()
        except EOFError:
            break
        if not line:
            continue
        low = line.lower()
        if low in {"quit", "exit"}:
            break

        if line.startswith("send "):
            # send <dst> <texto>
            try:
                _, dst, text = line.split(" ", 2)
            except ValueError:
                print("Uso: send <dst> <texto>")
                continue
            node.send_data(dst_node=dst, payload=text, hops=16, alg=node.algorithm)

        elif line.startswith("broadcast "):
            text = line[len("broadcast "):].strip()
            if not text:
                print("Uso: broadcast <texto>")
                continue
            node.send_data(dst_node="*", payload=text, hops=16, alg="flooding")

        elif line.startswith("ping "):
            dst = line.split(" ", 1)[1].strip()
            if not dst:
                print("Uso: ping <vecino>")
                continue
            node.send_hello(dst)
            time.sleep(0.7)
            rtt = node.neighbor_rtt_ms.get(dst)
            if rtt is not None:
                print(f"[{node.name}] RTT {node.name}↔{dst}: {rtt:.1f} ms")
            else:
                print(f"[{node.name}] RTT no disponible (¿llegó el ECHO?)")

        elif line.startswith("info"):
            parts = line.split()
            only = parts[1] if len(parts) >= 2 else None
            if node.algorithm == "lsr":
                node._emit_info_lsr(only_to=only)
                print(f"[{node.name}] INFO/LSR emitido{' a '+only if only else ' a todos'}.")
            elif node.algorithm == "dvr":
                if only:
                    # DVR dirigido a un vecino específico
                    vec = node.dvr.announce_for(only)  # split-horizon/poison-reverse aplicado
                    node.dvr_seq += 1
                    msg = Message.info_lsr(
                        src=node.name, seq_num=node.dvr_seq,
                        neighbors=vec, hops=16, alg="dvr"
                    )
                    node.send_to_node(only, msg)
                    print(f"[{node.name}] INFO/DVR emitido a {only}.")
                else:
                    node._emit_info_dvr()
                    print(f"[{node.name}] INFO/DVR emitido a todos.")
            else:
                print(f"[{node.name}] Flooding no usa INFO manual.")

        elif low == "show routes":
            print(node.route_table)

        elif low == "show neighbors":
            print(node.neighbor_ids)

        elif low == "show lsdb":
            lsdb = node.get_lsdb()
            if not lsdb:
                print("(LSDB vacía)")
            else:
                for u in sorted(lsdb.keys()):
                    nbrs = lsdb[u]
                    if not nbrs:
                        print(f"  {u}: (sin vecinos)")
                    else:
                        for v in sorted(nbrs.keys()):
                            print(f"  {u} -> {v}  costo={nbrs[v]}")

        elif low == "show dv":
            if node.algorithm != "dvr":
                print("(No estás en DVR. Usa --alg dvr)")
            else:
                dt = node.dvr.get_distance_table()
                if not dt:
                    print("(DV vacío)")
                else:
                    print("dest\t\tdist\t\tnext_hop")
                    for d in sorted(dt.keys()):
                        dist, nh = dt[d]
                        dist_str = "∞" if dist >= 1e12 else f"{dist:.3f}"
                        print(f"{d}\t\t{dist_str}\t\t{nh}")

        elif low == "nodes":
            print("Nodos conocidos:")
            for k in sorted(node.channel_by_node.keys()):
                ch = node.channel_by_node[k]
                state = "🟢 vecino" if k in node.neighbor_ids else "—"
                print(f"  {k:>3} | canal={ch} | {state}")

        else:
            print(f"Comando desconocido: {line}")


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
    ap.add_argument("--redis-user", default=None)   # opcional (ACL)
    ap.add_argument("--redis-pass", default="")

    # Modo envío exprés (en multinodo)
    ap.add_argument("--send", default="", help="Envía un mensaje: SRC:DST:Texto (ej. A:C:'Hola')")
    ap.add_argument("--run-seconds", type=int, default=40, help="Segundos a mantener la demo viva")

    # Modo shell por nodo
    ap.add_argument("--src", help="ID del nodo para shell (ej. B)")
    ap.add_argument("--shell", action="store_true", help="Inicia shell interactivo para --src")

    args = ap.parse_args()

    # Cargar names/topo
    names = load_names(args.names)
    topo = load_topo(args.topo)

    # Redis cfg
    redis_cfg = dict(host=args.redis_host, port=args.redis_port, password=args.redis_pass)
    if args.redis_user:
        redis_cfg["username"] = args.redis_user

    # --- MODO SHELL (un nodo) ---
    if args.src and args.shell:
        if args.src not in names:
            print(f"--src '{args.src}' no existe en {args.names}")
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
            time.sleep(3)  # deja que se propaguen HELLO/INFO un poco
            shell_loop(node)
        finally:
            node.stop()
        return

    # --- MODO MULTINODO (todos) ---
    nodes = build_nodes(names, topo, args.alg, args.transport, redis_cfg)

    try:
        time.sleep(4)  # tiempo para HELLO/INFO

        if args.send:
            try:
                src, dst, text = args.send.split(":", 2)
                if src not in nodes:
                    print(f"SRC '{src}' no existe en names.")
                else:
                    print(f"Enviando '{text}' de {src} a {dst} (alg={args.alg})")
                    nodes[src].send_data(dst_node=dst, payload=text, hops=16, alg=args.alg)
            except ValueError:
                print("--send debe ser 'SRC:DST:Texto' (usa comillas si hay espacios)")

        # Mantener vivo para observar forwarding
        t0 = time.time()
        while time.time() - t0 < args.run_seconds:
            time.sleep(0.5)

    except KeyboardInterrupt:
        pass
    finally:
        stop_nodes(nodes)


if __name__ == "__main__":
    main()

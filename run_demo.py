import json, time, threading, signal, sys
from router.node import Node

def load_json(path):
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

def main():
    topo = load_json("topo-sample.json")["config"]
    names = load_json("names-sample.json")["config"]

    nodes = {}
    for name, cfg in names.items():
        neighbors = topo.get(name, [])
        n = Node(name=name, bind_host=cfg["host"], bind_port=cfg["port"], names=names, neighbors=neighbors)
        nodes[name] = n
        n.start()

    def stop_all(*args):
        for n in nodes.values():
            n.stop()
        time.sleep(0.5)
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_all)

    print("[demo] Esperando 3s a que se propaguen LSPs y se estabilicen rutas...")
    time.sleep(3)
    print("[demo] Tabla de ruteo (aprox)")
    for k,n in nodes.items():
        print(k, "→", n.routing_table)

    print("[demo] Enviando DATA A→D ...")
    nodes["A"].send_data("D", "Hola desde A", ttl=10)

    # Mantener corriendo para ver hellos/echos
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()

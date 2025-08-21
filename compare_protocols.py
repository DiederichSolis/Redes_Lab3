#!/usr/bin/env python3
"""
Script para comparar protocolos de enrutamiento LSR vs DVR.
Ejecuta ambos protocolos en la misma topología y compara resultados.
Incluye métricas automáticas y análisis de rendimiento.
"""

import json
import time
import threading
import signal
import sys
from router.node import Node
from router.metrics import collector

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def create_network(protocol="lsr"):
    """Crea una red completa con el protocolo especificado."""
    topo = load_json("topo-sample.json")["config"]
    names = load_json("names-sample.json")["config"]
    
    nodes = {}
    for name, cfg in names.items():
        neighbors = topo.get(name, [])
        n = Node(
            name=name,
            bind_host=cfg["host"],
            bind_port=cfg["port"],
            names=names,
            neighbors=neighbors,
            routing_protocol=protocol
        )
        nodes[name] = n
        n.start()
    
    return nodes

def cleanup_network(nodes):
    """Detiene y limpia una red de nodos."""
    for node in nodes.values():
        node.stop()
    time.sleep(1)

def run_protocol_test(protocol_name, protocol_type):
    """Ejecuta una prueba completa con un protocolo específico y métricas."""
    print(f"\n[test] ===== INICIANDO PRUEBA {protocol_name} =====")
    
    # Crear red
    nodes = create_network(protocol_type)
    
    # Esperar estabilización inicial
    print(f"[test] {protocol_name}: Esperando convergencia inicial...")
    time.sleep(8)
    
    # Mostrar tablas de enrutamiento
    print(f"[test] {protocol_name}: Tablas de enrutamiento:")
    routing_tables = {}
    for name, node in nodes.items():
        routing_tables[name] = node.routing_table.copy()
        routes_count = len([r for r in node.routing_table.values() if r])
        print(f"  {name}: {routes_count} rutas → {node.routing_table}")
    
    # Fase 1: Envío de datos básico
    print(f"[test] {protocol_name}: Fase 1 - Datos básicos...")
    basic_tests = [
        ("A", "D", f"Test {protocol_name} A→D"),
        ("B", "C", f"Test {protocol_name} B→C"),
        ("C", "A", f"Test {protocol_name} C→A"),
        ("D", "B", f"Test {protocol_name} D→B"),
    ]
    
    for src, dst, msg in basic_tests:
        try:
            nodes[src].send_data(dst, msg, ttl=8)
            time.sleep(0.8)
        except Exception as e:
            print(f"[test] Error enviando {src}→{dst}: {e}")
    
    # Fase 2: Stress test
    print(f"[test] {protocol_name}: Fase 2 - Stress test...")
    for i in range(3):
        try:
            nodes["A"].send_data("D", f"{protocol_name}-Stress-{i}")
            nodes["B"].send_data("A", f"{protocol_name}-Stress-{i}")
            time.sleep(0.3)
        except Exception as e:
            print(f"[test] Error en stress test: {e}")
    
    # Esperar procesamiento final
    print(f"[test] {protocol_name}: Esperando finalización...")
    time.sleep(4)
    
    # Detener nodos
    cleanup_network(nodes)
    
    return routing_tables

def run_comparison():
    """Ejecuta la comparación completa entre LSR y DVR con métricas automáticas."""
    print("🚀 COMPARADOR DE PROTOCOLOS CON MÉTRICAS AUTOMÁTICAS")
    print("=" * 60)
    
    # Reinicializar colector de métricas
    global collector
    collector = collector.__class__()
    
    # Ejecutar prueba LSR
    print("\n📡 EJECUTANDO PRUEBA LSR...")
    lsr_tables = run_protocol_test("LSR (Dijkstra)", "lsr")
    
    # Esperar entre pruebas
    time.sleep(3)
    
    # Ejecutar prueba DVR
    print("\n📡 EJECUTANDO PRUEBA DVR...")
    dvr_tables = run_protocol_test("DVR (Bellman-Ford)", "dvr")
    
    # Mostrar métricas automáticas
    print("\n" + "="*60)
    collector.print_summary()
    
    # Comparar tablas de enrutamiento
    print("\n" + "="*60)
    print("🗺️  COMPARACIÓN DE TABLAS DE ENRUTAMIENTO")
    print("="*60)
    
    routing_identical = True
    for node_name in ["A", "B", "C", "D"]:
        print(f"\n🔹 {node_name}:")
        lsr_routes = lsr_tables.get(node_name, {})
        dvr_routes = dvr_tables.get(node_name, {})
        
        lsr_count = len([r for r in lsr_routes.values() if r])
        dvr_count = len([r for r in dvr_routes.values() if r])
        
        print(f"   LSR: {lsr_count} rutas → {lsr_routes}")
        print(f"   DVR: {dvr_count} rutas → {dvr_routes}")
        
        # Verificar si las rutas son iguales
        if lsr_routes == dvr_routes:
            print(f"   ✅ Rutas idénticas")
        else:
            print(f"   ❌ Rutas diferentes")
            routing_identical = False
            
            # Mostrar diferencias específicas
            all_dests = set(lsr_routes.keys()) | set(dvr_routes.keys())
            for dest in all_dests:
                lsr_nh = lsr_routes.get(dest, "N/A")
                dvr_nh = dvr_routes.get(dest, "N/A")
                if lsr_nh != dvr_nh:
                    print(f"     {dest}: LSR→{lsr_nh}, DVR→{dvr_nh}")
    
    # Generar reporte JSON
    report = collector.get_comparison_report()
    report["routing_comparison"] = {
        "identical": routing_identical,
        "lsr_tables": lsr_tables,
        "dvr_tables": dvr_tables
    }
    
    timestamp = int(time.time())
    report_file = f"comparison_report_{timestamp}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # Resumen final
    print("\n" + "="*60)
    print("🎯 RESUMEN EJECUTIVO")
    print("="*60)
    
    if len(collector.protocols) >= 2:
        protocols = list(collector.protocols.keys())
        conv_times = [collector.protocols[p].convergence_time for p in protocols]
        ctrl_msgs = [collector.protocols[p].control_messages for p in protocols]
        
        print(f"🏃 Convergencia más rápida: {protocols[conv_times.index(min(conv_times))]}")
        print(f"⚡ Protocolo más eficiente: {protocols[ctrl_msgs.index(min(ctrl_msgs))]}")
        print(f"📊 Ratio de overhead: {max(ctrl_msgs) / min(ctrl_msgs) if min(ctrl_msgs) > 0 else 'N/A':.2f}x")
    
    print(f"🗺️  Rutas consistentes: {'✅ SÍ' if routing_identical else '❌ NO'}")
    print(f"💾 Reporte guardado: {report_file}")
    print("="*60)

def main():
    """Función principal con manejo de señales."""
    def signal_handler(sig, frame):
        print("\n🛑 Interrupción recibida. Saliendo...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        run_comparison()
    except Exception as e:
        print(f"\n❌ Error durante la ejecución: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n👋 Comparación finalizada.")

if __name__ == "__main__":
    main()
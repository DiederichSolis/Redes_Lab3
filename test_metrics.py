#!/usr/bin/env python3
"""
Script de prueba rápida para verificar el sistema de métricas.
Ejecuta una prueba corta y muestra las métricas automáticas.
"""

import json
import time
import sys
from router.node import Node
from router.metrics import collector

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def test_metrics():
    """Prueba rápida del sistema de métricas."""
    print("🧪 PRUEBA RÁPIDA DEL SISTEMA DE MÉTRICAS")
    print("=" * 50)
    
    # Cargar configuración
    topo = load_json("topo-sample.json")["config"]
    names = load_json("names-sample.json")["config"]
    
    # Crear solo dos nodos para prueba rápida
    nodes = {}
    
    print("\n📡 Creando nodos de prueba...")
    
    # Nodo A con LSR
    cfg = names["A"]
    neighbors = topo.get("A", [])
    node_a = Node(
        name="A",
        bind_host=cfg["host"],
        bind_port=cfg["port"],
        names=names,
        neighbors=neighbors,
        routing_protocol="lsr"
    )
    nodes["A"] = node_a
    node_a.start()
    
    # Nodo B con DVR
    cfg = names["B"]
    neighbors = topo.get("B", [])
    node_b = Node(
        name="B",
        bind_host=cfg["host"],
        bind_port=cfg["port"],
        names=names,
        neighbors=neighbors,
        routing_protocol="dvr"
    )
    nodes["B"] = node_b
    node_b.start()
    
    print("✅ Nodos creados: A (LSR), B (DVR)")
    
    # Esperar convergencia inicial
    print("\n⏳ Esperando convergencia inicial (5s)...")
    time.sleep(5)
    
    # Enviar algunos mensajes
    print("\n📨 Enviando mensajes de prueba...")
    
    try:
        # LSR: A -> B
        nodes["A"].send_data("B", "Mensaje LSR: A -> B")
        time.sleep(1)
        
        # DVR: B -> A
        nodes["B"].send_data("A", "Mensaje DVR: B -> A")
        time.sleep(1)
        
        # Más mensajes para generar métricas
        for i in range(3):
            nodes["A"].send_data("B", f"Test LSR {i}")
            nodes["B"].send_data("A", f"Test DVR {i}")
            time.sleep(0.5)
        
        print("✅ Mensajes enviados")
        
    except Exception as e:
        print(f"❌ Error enviando mensajes: {e}")
    
    # Esperar procesamiento
    print("\n⏳ Esperando procesamiento (3s)...")
    time.sleep(3)
    
    # Mostrar métricas
    print("\n" + "="*50)
    collector.print_summary()
    
    # Mostrar tablas de enrutamiento
    print("\n🗺️  TABLAS DE ENRUTAMIENTO:")
    for name, node in nodes.items():
        protocol = "LSR" if node.routing_protocol == "lsr" else "DVR"
        routes_count = len([r for r in node.routing_table.values() if r])
        print(f"   {name} ({protocol}): {routes_count} rutas → {node.routing_table}")
    
    # Generar reporte
    report = collector.get_comparison_report()
    timestamp = int(time.time())
    report_file = f"test_metrics_{timestamp}.json"
    
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Reporte guardado: {report_file}")
    except Exception as e:
        print(f"❌ Error guardando reporte: {e}")
    
    # Limpiar
    print("\n🧹 Limpiando...")
    for node in nodes.values():
        node.stop()
    time.sleep(1)
    
    print("✅ Prueba completada exitosamente!")

def main():
    try:
        test_metrics()
    except KeyboardInterrupt:
        print("\n🛑 Prueba interrumpida por el usuario.")
    except Exception as e:
        print(f"\n❌ Error durante la prueba: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n👋 Prueba finalizada.")

if __name__ == "__main__":
    main()

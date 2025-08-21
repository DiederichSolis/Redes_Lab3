from __future__ import annotations
import time
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

class MetricType(Enum):
    CONVERGENCE_TIME = "convergence_time"
    MESSAGE_COUNT = "message_count"
    ROUTE_CHANGES = "route_changes"
    MEMORY_USAGE = "memory_usage"
    CPU_USAGE = "cpu_usage"

@dataclass
class ProtocolMetrics:
    """Métricas específicas para un protocolo de enrutamiento."""
    protocol: str
    start_time: float
    convergence_time: float = 0.0
    total_messages: int = 0
    control_messages: int = 0
    data_messages: int = 0
    route_changes: int = 0
    final_routes: Dict[str, str] = field(default_factory=dict)
    
    def is_converged(self, current_routes: Dict[str, str], stable_threshold: float = 2.0) -> bool:
        """Verifica si el protocolo ha convergido (rutas estables por threshold segundos)."""
        if not self.final_routes:
            self.final_routes = current_routes.copy()
            return False
        
        # Si las rutas cambiaron, resetear el timer
        if self.final_routes != current_routes:
            self.final_routes = current_routes.copy()
            self.route_changes += 1
            return False
        
        # Verificar si han pasado threshold segundos sin cambios
        time_since_change = time.time() - self.start_time
        if time_since_change >= stable_threshold:
            if self.convergence_time == 0.0:
                self.convergence_time = time_since_change
            return True
        
        return False

class MetricsCollector:
    """Colector central de métricas para todos los protocolos."""
    
    def __init__(self):
        self.protocols: Dict[str, ProtocolMetrics] = {}
        self.network_events: List[Dict[str, Any]] = []
        self.start_time = time.time()
    
    def register_protocol(self, protocol: str, node_name: str):
        """Registra un nuevo protocolo para monitoreo."""
        if protocol not in self.protocols:
            self.protocols[protocol] = ProtocolMetrics(
                protocol=protocol,
                start_time=time.time()
            )
        print(f"[metrics] Registrado protocolo: {protocol} en nodo {node_name}")
    
    def record_message(self, protocol: str, message_type: str, src: str, dst: str):
        """Registra un mensaje enviado/recibido."""
        if protocol in self.protocols:
            self.protocols[protocol].total_messages += 1
            
            if message_type in ["lsp", "dv_announcement", "hello", "echo"]:
                self.protocols[protocol].control_messages += 1
            elif message_type == "data":
                self.protocols[protocol].data_messages += 1
            
            # Registrar evento de red
            self.network_events.append({
                "timestamp": time.time(),
                "protocol": protocol,
                "type": message_type,
                "src": src,
                "dst": dst
            })
    
    def check_convergence(self, protocol: str, node_name: str, current_routes: Dict[str, str]) -> bool:
        """Verifica convergencia de un protocolo específico."""
        if protocol in self.protocols:
            return self.protocols[protocol].is_converged(current_routes)
        return False
    
    def get_protocol_summary(self, protocol: str) -> Dict[str, Any]:
        """Obtiene resumen de métricas para un protocolo."""
        if protocol not in self.protocols:
            return {}
        
        metrics = self.protocols[protocol]
        return {
            "protocol": protocol,
            "convergence_time": metrics.convergence_time,
            "total_messages": metrics.total_messages,
            "control_messages": metrics.control_messages,
            "data_messages": metrics.data_messages,
            "route_changes": metrics.route_changes,
            "final_routes": metrics.final_routes.copy()
        }
    
    def get_comparison_report(self) -> Dict[str, Any]:
        """Genera reporte de comparación entre protocolos."""
        report = {
            "timestamp": time.time(),
            "total_runtime": time.time() - self.start_time,
            "protocols": {},
            "comparison": {}
        }
        
        # Métricas por protocolo
        for protocol, metrics in self.protocols.items():
            report["protocols"][protocol] = self.get_protocol_summary(protocol)
        
        # Comparaciones
        if len(self.protocols) > 1:
            protocols = list(self.protocols.keys())
            
            # Tiempo de convergencia
            convergence_times = [self.protocols[p].convergence_time for p in protocols]
            fastest = protocols[convergence_times.index(min(convergence_times))]
            slowest = protocols[convergence_times.index(max(convergence_times))]
            
            # Overhead de mensajes
            control_messages = [self.protocols[p].control_messages for p in protocols]
            most_efficient = protocols[control_messages.index(min(control_messages))]
            least_efficient = protocols[control_messages.index(max(control_messages))]
            
            report["comparison"] = {
                "fastest_convergence": fastest,
                "slowest_convergence": slowest,
                "most_efficient": most_efficient,
                "least_efficient": least_efficient,
                "convergence_ratio": max(convergence_times) / min(convergence_times) if min(convergence_times) > 0 else float('inf'),
                "overhead_ratio": max(control_messages) / min(control_messages) if min(control_messages) > 0 else float('inf')
            }
        
        return report
    
    def print_summary(self):
        """Imprime resumen de métricas en consola."""
        print("\n" + "="*60)
        print("📊 REPORTE DE MÉTRICAS DE ENRUTAMIENTO")
        print("="*60)
        
        for protocol, metrics in self.protocols.items():
            print(f"\n🔹 {protocol.upper()}:")
            print(f"   ⏱️  Tiempo de convergencia: {metrics.convergence_time:.2f}s")
            print(f"   📨 Total mensajes: {metrics.total_messages}")
            print(f"   🎛️  Mensajes de control: {metrics.control_messages}")
            print(f"   📦 Mensajes de datos: {metrics.data_messages}")
            print(f"   🔄 Cambios de ruta: {metrics.route_changes}")
            print(f"   🎯 Rutas finales: {len(metrics.final_routes)}")
        
        # Comparación si hay múltiples protocolos
        if len(self.protocols) > 1:
            report = self.get_comparison_report()
            comparison = report["comparison"]
            
            print(f"\n🔍 COMPARACIÓN:")
            print(f"   🏃 Convergencia más rápida: {comparison['fastest_convergence']}")
            print(f"   🐌 Convergencia más lenta: {comparison['slowest_convergence']}")
            print(f"   ⚡ Más eficiente: {comparison['most_efficient']}")
            print(f"   💸 Menos eficiente: {comparison['least_efficient']}")
            print(f"   📈 Ratio convergencia: {comparison['convergence_ratio']:.2f}x")
            print(f"   📊 Ratio overhead: {comparison['overhead_ratio']:.2f}x")
        
        print("="*60)

# Instancia global del colector
collector = MetricsCollector()

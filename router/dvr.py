from __future__ import annotations
from typing import Dict, Tuple, List
import copy

def bellman_ford(graph: Dict[str, Dict[str, float]], source: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Algoritmo de Bellman-Ford para Distance Vector Routing.
    
    Args:
        graph: Grafo dirigido con pesos {u: {v: w, ...}, ...}
        source: Nodo origen
    
    Returns:
        (dist, prev) donde dist[v] = distancia mínima, prev[v] = predecesor
    """
    # Inicialización
    dist = {v: float('inf') for v in graph}
    prev = {v: None for v in graph}
    dist[source] = 0.0
    
    # Relajación de aristas |V|-1 veces
    for _ in range(len(graph) - 1):
        for u in graph:
            for v, weight in graph[u].items():
                if dist[u] != float('inf') and dist[u] + weight < dist[v]:
                    dist[v] = dist[u] + weight
                    prev[v] = u
    
    # Detección de ciclos negativos (opcional para este lab)
    for u in graph:
        for v, weight in graph[u].items():
            if dist[u] != float('inf') and dist[u] + weight < dist[v]:
                print(f"⚠️  Ciclo negativo detectado en {u} -> {v}")
                # En caso de ciclo negativo, marcamos como inalcanzable
                dist[v] = float('inf')
                prev[v] = None
    
    return dist, prev

def next_hop_for_dvr(dest: str, source: str, prev: Dict[str, str]) -> str | None:
    """
    Encuentra el next hop para DVR recorriendo predecesores.
    Similar a Dijkstra pero optimizado para DVR.
    """
    if dest == source:
        return source
    
    if prev.get(dest) is None:
        return None
    
    cur = dest
    path = [cur]
    
    # Recorrer predecesores hasta encontrar el vecino inmediato
    while prev.get(cur) is not None and prev[cur] != source:
        cur = prev[cur]
        path.append(cur)
        if len(path) > 1000:  # Protección contra loops
            break
    
    # El next hop es el último nodo antes de source
    if path and prev.get(path[-1]) == source:
        return path[-1]
    
    # Fallback: si no hay ruta clara, usar el predecesor directo
    return prev.get(dest)

class DistanceVectorRouter:
    """
    Router que implementa Distance Vector Routing.
    Mantiene tabla de distancias y actualiza periódicamente.
    """
    
    def __init__(self, node_name: str):
        self.node_name = node_name
        # Tabla de distancias: {destino: (distancia, next_hop)}
        self.distance_table: Dict[str, Tuple[float, str]] = {}
        # Tabla de vecinos y sus anuncios
        self.neighbor_announcements: Dict[str, Dict[str, float]] = {}
        # Costos de enlaces directos
        self.direct_costs: Dict[str, float] = {}
        
    def update_direct_link(self, neighbor: str, cost: float):
        """Actualiza el costo de un enlace directo."""
        self.direct_costs[neighbor] = cost
        self._recompute_distances()
    
    def receive_announcement(self, from_neighbor: str, distances: Dict[str, float]):
        """Recibe anuncio de distancia de un vecino."""
        self.neighbor_announcements[from_neighbor] = distances.copy()
        self._recompute_distances()
    
    def _recompute_distances(self):
        """Recalcula todas las distancias usando Bellman-Ford."""
        # Construir grafo completo para Bellman-Ford
        graph = self._build_complete_graph()
        
        try:
            dist, prev = bellman_ford(graph, self.node_name)
            
            # Actualizar tabla de distancias
            new_distance_table = {}
            for dest in dist:
                if dest == self.node_name:
                    continue
                if dist[dest] != float('inf'):
                    next_hop = next_hop_for_dvr(dest, self.node_name, prev)
                    if next_hop:
                        new_distance_table[dest] = (dist[dest], next_hop)
            
            self.distance_table = new_distance_table
            
        except Exception as e:
            print(f"[{self.node_name}] Error en DVR: {e}")
    
    def _build_complete_graph(self) -> Dict[str, Dict[str, float]]:
        """Construye grafo completo combinando enlaces directos y anuncios de vecinos."""
        graph = {self.node_name: {}}
        
        # Agregar enlaces directos
        for neighbor, cost in self.direct_costs.items():
            graph[self.node_name][neighbor] = cost
            if neighbor not in graph:
                graph[neighbor] = {}
            graph[neighbor][self.node_name] = cost  # Simetría
        
        # Agregar rutas anunciadas por vecinos
        for neighbor, announcements in self.neighbor_announcements.items():
            if neighbor not in graph:
                graph[neighbor] = {}
            
            for dest, cost in announcements.items():
                if dest != self.node_name:  # No crear loops
                    graph[neighbor][dest] = cost
                    if dest not in graph:
                        graph[dest] = {}
        
        return graph
    
    def get_next_hop(self, destination: str) -> str | None:
        """Obtiene el next hop para un destino."""
        if destination in self.distance_table:
            return self.distance_table[destination][1]
        return None
    
    def get_distance(self, destination: str) -> float:
        """Obtiene la distancia a un destino."""
        if destination in self.distance_table:
            return self.distance_table[destination][0]
        return float('inf')
    
    def get_routing_table(self) -> Dict[str, str]:
        """Retorna tabla de enrutamiento en formato {dest: next_hop}."""
        return {dest: next_hop for dest, (_, next_hop) in self.distance_table.items()}
    
    def get_distance_table(self) -> Dict[str, Tuple[float, str]]:
        """Retorna tabla completa de distancias."""
        return self.distance_table.copy()
    
    def announce_distances(self) -> Dict[str, float]:
        """Prepara anuncio de distancias para vecinos."""
        announcement = {}
        for dest, (dist, _) in self.distance_table.items():
            announcement[dest] = dist
        return announcement

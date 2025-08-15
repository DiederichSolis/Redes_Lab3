from __future__ import annotations
from typing import Dict, Tuple, List
import heapq

def dijkstra(graph: Dict[str, Dict[str, float]], source: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    graph: dict de dicts con pesos positivos: {u: {v: w, ...}, ...}
    source: nodo origen
    return: (dist, prev) donde dist[v] = distancia mínima, prev[v] = predecesor
    """
    dist = {v: float('inf') for v in graph}
    prev = {v: None for v in graph}
    dist[source] = 0.0
    pq: List[Tuple[float, str]] = [(0.0, source)]
    while pq:
        d,u = heapq.heappop(pq)
        if d>dist[u]: 
            continue
        for v,w in graph.get(u,{}).items():
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq,(nd,v))
    return dist, prev

def next_hop_for(dest: str, source: str, prev: Dict[str, str]) -> str | None:
    """
    Recorre predecesores desde dest hasta encontrar el vecino inmediato de source.
    """
    if dest == source:
        return source
    cur = dest
    if prev.get(cur) is None:
        return None
    path = [cur]
    while prev.get(cur) is not None and prev[cur] != source:
        cur = prev[cur]
        path.append(cur)
        if len(path) > 10000:  # protección
            break
    return path[-1] if prev.get(path[-1]) == source else (prev.get(dest) if prev.get(dest) else None)

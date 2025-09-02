# dvr.py
from __future__ import annotations
from typing import Dict, Tuple, Optional

INF = 1e12  # "infinito" práctico para DV


class DistanceVectorRouter:
    """
    Implementación minimal de Distance Vector (Bellman-Ford distribuido).
    - Mantiene:
        * costos directos: c(x,v)
        * anuncios de vecinos: D_v(*)
        * tabla local: D_x(dest) = (dist, next_hop)
    - Soporta split-horizon / poison-reverse opcionales.
    - Sin temporizadores de hold-down (se pueden agregar luego).
    """

    def __init__(self, node_name: str,
                 split_horizon: bool = True,
                 poison_reverse: bool = False):
        self.node = node_name
        self.split_horizon = split_horizon
        self.poison_reverse = poison_reverse

        # D_x(dest) -> (dist, next_hop)
        self.dv: Dict[str, Tuple[float, Optional[str]]] = {self.node: (0.0, None)}
        # c(x,v)
        self.direct_cost: Dict[str, float] = {}
        # Anuncios recibidos de cada vecino: neighbor -> {dest: dist}
        self.recv_from_neighbor: Dict[str, Dict[str, float]] = {}

    # ---------- util ----------
    def _ensure_self(self):
        if self.node not in self.dv:
            self.dv[self.node] = (0.0, None)

    def _set_direct(self, neighbor: str, cost: float):
        self.direct_cost[neighbor] = float(cost)

    # ---------- API: enlaces directos ----------
    def update_direct_link(self, neighbor: str, cost: Optional[float]):
        """
        Define/actualiza el costo c(x,neighbor). Si cost es None, elimina el enlace (∞).
        Dispara recomputación.
        """
        if cost is None:
            # enlace caído
            if neighbor in self.direct_cost:
                del self.direct_cost[neighbor]
        else:
            self._set_direct(neighbor, cost)
        self.recompute()

    # ---------- API: anuncios recibidos ----------
    def receive_announcement(self, from_neighbor: str, vector: Dict[str, float]):
        """
        Guarda el vector D_from_neighbor(*) recibido (p. ej., por INFO/alg=dvr).
        Distancias inválidas/ausentes se tratan como INF.
        """
        self.recv_from_neighbor[from_neighbor] = dict(vector or {})
        self.recompute()

    # ---------- Recomputación DV ----------
    def recompute(self):
        """
        Aplica la ecuación DV con la info local actual:
           D_x(y) = min_v [ c(x,v) + D_v(y) ]
        """
        self._ensure_self()

        # Inicializa con infinito
        new_dv: Dict[str, Tuple[float, Optional[str]]] = {self.node: (0.0, None)}

        # Siempre considerar vecinos directos como candidatos a destinos
        candidates = set(self.direct_cost.keys())

        # y todos los destinos que aparecen en anuncios de vecinos
        for n, vec in self.recv_from_neighbor.items():
            candidates.update(vec.keys())

        # para cada destino y, calcula el mejor next_hop v
        for dest in candidates:
            best_cost = INF
            best_nh: Optional[str] = None

            for v, c_xv in self.direct_cost.items():
                # D_v(y) recibido; si no hay info, INF
                dv_vy = self.recv_from_neighbor.get(v, {}).get(dest, INF)

                # split-horizon / poison-reverse:
                # si la mejor ruta actual a 'dest' usaba 'v', entonces cuando
                # anunciemos a 'v' aplicaremos split/poison; aquí NO bloqueamos cálculo.
                cost = c_xv + dv_vy
                if cost < best_cost:
                    best_cost, best_nh = cost, v

            # Si 'dest' es vecino directo, compara con el enlace directo puro (camino 1 salto)
            if dest in self.direct_cost and self.direct_cost[dest] < best_cost:
                best_cost = self.direct_cost[dest]
                best_nh = dest

            # Si es yo mismo
            if dest == self.node:
                best_cost, best_nh = 0.0, None

            # Guarda resultado (si no se aprendió nada, quedará INF y next_hop None)
            new_dv[dest] = (best_cost, best_nh)

        # Limpieza: si algo quedó en INF y no es vecino ni yo, puedes omitirlo
        cleaned: Dict[str, Tuple[float, Optional[str]]] = {}
        for d, (dist, nh) in new_dv.items():
            if d == self.node or d in self.direct_cost or dist < INF:
                cleaned[d] = (dist, nh)

        self.dv = cleaned

    # ---------- Lecturas ----------
    def get_distance(self, dest: str) -> float:
        return self.dv.get(dest, (INF, None))[0]

    def get_next_hop(self, dest: str) -> Optional[str]:
        return self.dv.get(dest, (INF, None))[1]

    def get_routing_table(self) -> Dict[str, str]:
        table: Dict[str, str] = {}
        for d, (dist, nh) in self.dv.items():
            if d == self.node:
                continue
            if dist < INF and nh is not None:
                table[d] = nh
        return table

    def get_distance_table(self) -> Dict[str, Tuple[float, Optional[str]]]:
        return dict(self.dv)

    # ---------- Anuncios hacia vecinos ----------
    def announce_for(self, to_neighbor: str) -> Dict[str, float]:
        """
        Vector que debo anunciar a 'to_neighbor', aplicando split-horizon/poison-reverse.
        - split-horizon: no anuncio rutas cuyo next_hop == to_neighbor
        - poison-reverse: anuncio esas rutas con INF en lugar de ocultarlas
        """
        out: Dict[str, float] = {}
        for d, (dist, nh) in self.dv.items():
            if d == self.node:
                out[d] = 0.0
                continue
            if nh == to_neighbor:
                if self.poison_reverse:
                    out[d] = INF
                elif self.split_horizon:
                    continue
                else:
                    out[d] = dist
            else:
                out[d] = dist
        return out

    def announce_broadcast(self) -> Dict[str, float]:
        """
        Vector genérico (sin split-horizon). Útil si tu transporte no permite
        personalizar por vecino. Si puedes personalizar, usa announce_for().
        """
        return {d: dist for d, (dist, _nh) in self.dv.items()}

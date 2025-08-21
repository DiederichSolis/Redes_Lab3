# Routing Lab Prototype (Python)
Prototipo completo con **múltiples algoritmos de enrutamiento** y **métricas automáticas**:
- **Dijkstra (LSR)** en `router/dijkstra.py`
- **Bellman-Ford (DVR)** en `router/dvr.py`
- **Sistema de métricas automáticas** en `router/metrics.py`
- **Infraestructura de sockets UDP** + **hilos** con separación **Forwarding** y **Routing**
- **Sistema de mensajes JSON** con soporte para múltiples protocolos
- **Comparación LSR vs DVR** con análisis de rendimiento en tiempo real

> **Estado:** ✅ **COMPLETADO** - Implementación funcional de LSR y DVR con métricas automáticas.

## Requisitos
- Python 3.10+
- No dependencias externas

## Cómo probar

### Demo básico con métricas (LSR + DVR mixto)
```bash
python run_demo.py
```
- Nodos A, B: LSR (Dijkstra)
- Nodos C, D: DVR (Bellman-Ford)
- **Métricas automáticas** al finalizar (Ctrl+C)
- Genera reporte JSON con estadísticas

### Comparación completa con análisis
```bash
python compare_protocols.py
```
- Ejecuta LSR y DVR por separado
- **Métricas automáticas** de rendimiento
- Compara tiempo de convergencia, overhead, tablas de enrutamiento
- Genera reporte JSON detallado

### Prueba rápida del sistema
```bash
python test_metrics.py
```
- Prueba rápida con 2 nodos
- Verifica que las métricas funcionen correctamente
- Ideal para debugging

## Estructura
```
routing_lab_proto/
  router/
    __init__.py
    message.py          # Sistema de mensajes + constantes
    dijkstra.py         # Algoritmo de Dijkstra (LSR)
    dvr.py             # Algoritmo de Bellman-Ford (DVR)
    metrics.py         # Sistema de métricas automáticas
    node.py            # Nodo con soporte multi-protocolo
  topo-sample.json     # Topología de ejemplo
  names-sample.json    # Configuración de nodos
  run_demo.py          # Demo mixto LSR/DVR con métricas
  compare_protocols.py # Comparador completo con análisis
  test_metrics.py      # Prueba rápida del sistema
  README.md
```

## Protocolos Implementados

### 1. LSR (Link State Routing) - Dijkstra
- **Convergencia:** Rápida (O(V²))
- **Overhead:** Moderado (LSP flooding)
- **Escalabilidad:** Buena para redes medianas
- **Implementación:** `router/dijkstra.py`

### 2. DVR (Distance Vector Routing) - Bellman-Ford
- **Convergencia:** Más lenta (O(V×E))
- **Overhead:** Menor (anuncios de distancia)
- **Escalabilidad:** Limitada (count-to-infinity)
- **Implementación:** `router/dvr.py`

### 3. Flooding
- **Convergencia:** Inmediata
- **Overhead:** Alto (envío a todos)
- **Escalabilidad:** Limitada
- **Implementación:** Integrado en `node.py`

## Mensajes (JSON)
```json
{
  "proto": "lsr|dvr|flooding|sys",
  "type": "hello|lsp|data|echo|info|dv_announcement",
  "from": "A",
  "to": "D",
  "ttl": 8,
  "headers": {},
  "payload": {}
}
```

## Características Técnicas

### ✅ Implementado
- **Multi-protocolo:** LSR, DVR, Flooding
- **Métricas automáticas** de rendimiento
- **Sockets UDP** con threading
- **Separación Forwarding/Routing**
- **TTL** para evitar loops
- **Hello/Echo** para RTT
- **Supresión de duplicados**
- **Comparación automática** de protocolos
- **Reportes JSON** detallados

### 🔧 Configuración
```python
# Crear nodo con protocolo específico
node = Node(
    name="A",
    bind_host="127.0.0.1",
    bind_port=56001,
    names=names,
    neighbors=["B", "C"],
    routing_protocol="dvr"  # "lsr" o "dvr"
)
```

## Experimentos Sugeridos

1. **Cambiar protocolos dinámicamente**
2. **Simular fallos de enlaces**
3. **Medir tiempo de convergencia**
4. **Comparar overhead de mensajes**
5. **Escalar a más nodos**

## Resultados Esperados

- **LSR:** Convergencia rápida, rutas óptimas
- **DVR:** Convergencia más lenta, mismas rutas finales
- **Ambos:** Deberían converger a las mismas rutas óptimas
- **Flooding:** Entrega garantizada pero con alto overhead



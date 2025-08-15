# Routing Lab Prototype (Python)
Pequeño prototipo **listo para correr** con:
- Dijkstra (LSR base) en `router/dijkstra.py`
- Infraestructura de **sockets TCP** + **hilos** con separación **Forwarding** y **Routing** en `router/node.py`
- Esquema de mensajes JSON en `router/message.py`
- Demo local multi‑nodo en `run_demo.py` que lanza 4 nodos (A,B,C,D) y envía un DATA de A→D pasando por los *next hops* calculados con Dijkstra.
- Archivos de ejemplo `topo-sample.json` y `names-sample.json`.

> **Estado:** avance funcional para mostrar hoy. Puedes extenderlo para Flooding/DVR más adelante.

## Requisitos
- Python 3.10+
- No dependencias externas

## Cómo probar
En una terminal, ejecuta:

```bash
python run_demo.py
```

Verás cómo se levantan 4 nodos, intercambian **LSPs** (Link-State Packets), calculan rutas con **Dijkstra** y rotean un **DATA** desde A hasta D con `ttl` decreciente.

## Estructura
```
routing_lab_proto/
  router/
    __init__.py
    message.py
    dijkstra.py
    node.py
  topo-sample.json
  names-sample.json
  run_demo.py
  README.md
```

## Mensajes (JSON)
Campos principales (más en `message.py`):
```json
{
  "proto": "dijkstra|flooding|lsr|dvr",
  "type": "hello|lsp|data|echo|info",
  "from": "A",
  "to": "D",
  "ttl": 8,
  "headers": {},
  "payload": {}
}
```

## Siguientes pasos sugeridos
- **Flooding**: reusar `forward_data` con registro de `msg_id` ya visto para evitar bucles.
- **Distance Vector**: agregar tabla de distancias por vecino, periodicidad de envío y *poisoned reverse*.
- **XMPP**: reemplazar `TCP sockets` por capa XMPP manteniendo la interfaz de `Node.send(msg)`.
- **Métricas**: tiempos de `HELLO/PING`, histogramas de RTT y cambios de tabla.

---
**Basado en el PDF del laboratorio (estructura y criterios).**

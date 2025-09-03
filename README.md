# Redes Lab 3 — Algoritmos de Enrutamiento (Parte 2)

> **Proyecto base del Grupo (Sec10) para Flooding, DVR y LSR sobre Redis**  
> Última actualización: 2025-09-03

Este repositorio implementa un **nodo de ruteo** con un shell interactivo y soporte para tres algoritmos:
- **Flooding** (difusión)
- **DVR** (*Distance Vector Routing*)
- **LSR** (*Link-State Routing* con Dijkstra)

El transporte por defecto es **Redis (pub/sub)**; también hay compatibilidad con **UDP** (según configuración). El proyecto está pensado para **interoperar** con otros equipos, usando un **formato de mensaje JSON común**, y para ejecutar **pruebas de topologías** como línea, anillo y árbol/estrella.

---

## 🧩 Estructura del proyecto

```
Redes_Lab3/
├── run_demo.py               # CLI para levantar nodos (shell o envío 'one-shot')
├── router/
│   ├── node.py               # Lógica del nodo: listener, forwarding, routing loops
│   ├── message.py            # Clase Message y serialización JSON
│   ├── dijkstra.py           # Cálculo de rutas para LSR
│   └── ...                   # utilidades y helpers
├── names-*.json              # Mapa lógico → canal (Redis)
├── topo-*.json               # Definición de vecindad (topologías)
└── README_Redes_Lab3.md      # Este documento
```

---

## 🔐 Protocolo de mensaje (interoperabilidad)

El proyecto usa un **JSON** tolerante (diccionario o lista de headers) para interoperar con otros grupos. Campos:

```jsonc
{
  "type": "hello|message|info|echo",
  "from": "sec10.grupoX.alumno",    // emisor lógico
  "to":   "sec10.grupoY.alumno",    // destino lógico
  "hops": 0,                        // contador de saltos
  "headers": [
    { "alg": "lsr|dvr|flooding" },  // algoritmo activo
    { "ttl": 8 },                   // tiempo de vida
    { "id":  "uuid-o-hash" }        // control de duplicados
  ],
  "seq_num":  0,                    // SOLO en type=info (LSR)
  "neighbors": { "B": 1.0 },        // SOLO en type=info (LSR)
  "payload": "texto o {{...}}"      // SOLO en type=message
}
```

> **Notas**  
> - `info` (LSR) anuncia `neighbors` y `seq_num`.  
> - `message` lleva `payload`.  
> - `hello/echo` se usan para salud y RTT.  
> - Compatibilidad: si `headers` llega como lista o dict, el nodo lo normaliza; `ttl` y `hops` se mantienen sincronizados.

---

## 🧰 Requisitos

- Python 3.10+ (probado con 3.11/3.12/3.13)
- Paquete `redis` (`pip install redis`)
- Acceso a un servidor Redis
  - **Lab**: `lab3.redesuvg.cloud:6379` (usuario `default`, pass `UVGRedis2025`)  
  - **Local** (opcional): `redis-server` en tu máquina

---

## ⚙️ Archivos de configuración

### `names-redis.json`
Mapea cada **nombre lógico** a un **canal Redis** (formato `sec10.grupoX.usuario`). Ejemplo:

```json
{
  "A": { "channel": "sec10.grupo7.Jorge" },
  "B": { "channel": "sec10.grupo7.diederich" },
  "C": { "channel": "sec10.grupo7.angel" },
  "D": { "channel": "sec10.grupo7.D" },
  "E": { "channel": "sec10.grupo7.E" },
  "F": { "channel": "sec10.grupo7.F" },
  "G": { "channel": "sec10.grupo7.G" }
}
```

### `topo-*.json`
Define la **vecindad** (grafo no dirigido). Ejemplo de **anillo** A–B–C–D–E–F–G–A:

```json
{
  "A": ["B","G"],
  "B": ["A","C"],
  "C": ["B","D"],
  "D": ["C","E"],
  "E": ["D","F"],
  "F": ["E","G"],
  "G": ["F","A"]
}
```

---

## 🚀 Cómo ejecutar

> En **zsh** usa una sola línea (evita `\` al final con espacios).  
> En cada caso abre **una terminal por nodo**.

### Modo interactivo (shell)

**LSR (recomendado para rutas mínimas):**

```bash
python3 run_demo.py --names names-redis.json --topo topo-anillo-7.json \
  --transport redis --alg lsr --src A --shell \
  --redis-host lab3.redesuvg.cloud --redis-port 6379 \
  --redis-user default --redis-pass UVGRedis2025
# Repite para B..G cambiando --src
```

**Flooding (difusión):**

```bash
python3 run_demo.py --names names-redis.json --topo topo-linea-5.json \
  --transport redis --alg flooding --src A --shell \
  --redis-host lab3.redesuvg.cloud --redis-port 6379 \
  --redis-user default --redis-pass UVGRedis2025
# Repite para B..E
```

**DVR (vector distancia):**

```bash
python3 run_demo.py --names names-redis.json --topo topo-linea-3.json \
  --transport redis --alg dvr --src A --shell \
  --redis-host lab3.redesuvg.cloud --redis-port 6379 \
  --redis-user default --redis-pass UVGRedis2025
# Repite para B y C
```

### Envío “one‑shot” (sin shell)

```bash
python3 run_demo.py --names names-redis.json --topo topo-anillo-7.json \
  --transport redis --alg lsr --src A \
  --send "F:Hola desde A (LSR)!" \
  --redis-host lab3.redesuvg.cloud --redis-port 6379 \
  --redis-user default --redis-pass UVGRedis2025
```

---

## 🖥️ Comandos del shell

- `send <dst> "<texto>"` — Unicast respetando el algoritmo activo.
- `broadcast <texto>` — Difusión por Flooding.
- `ping <vecino>` — Mide RTT con HELLO/ECHO.
- `info <vecino>` — Envía HELLO/INFO (según alg) a un vecino.
- `show routes` — Tabla de ruteo (LSR/DVR).
- `show neighbors` — Estado de vecinos (UP/DOWN, último ECHO ms).
- `show lsdb` — Base de enlaces (LSR).
- `show dv` — Vector distancia (DVR).
- `nodes` — Nodos conocidos y canales.
- `exit` — Salir del shell.

> **Tip:** espera ~3–5 s para que intercambien `INFO/HELLO` antes de `send` o `show routes` (convergencia).

---

## 🔎 Escenarios de prueba sugeridos

### 1) Línea (Flooding) — A–B–C–D–E
- `send E "hola"` desde A → **entregado**.
- Apaga **C** → repetir `send E` → **no entregado** (sin “saltos”).

### 2) Anillo (LSR) — A–B–C–D–E–F–G–A
- Ruta A→F debe ir por **G** (camino corto).  
- Apaga **G** → `show routes` en A ahora indica **B** como siguiente salto; `send F` se entrega por el otro lado (B–C–D–E).

### 3) Línea (DVR) — A–B–C
- Ver `show dv`/`show routes` hasta converger.  
- Enviar A→C. Apagar **B** → A pierde alcance a C hasta que se recupere B.

---

## 🧠 Detalles de implementación

- **Flooding:** reenvío a todos los vecinos con control de duplicados (`headers.id`) y `ttl/hops` decreciente.
- **LSR:** cada nodo difunde `info` con `{{seq_num, neighbors}}`; la LSDB se mantiene por el último `seq_num` por emisor y se corre **Dijkstra** para obtener `next-hop`.
- **DVR:** intercambio periódico de vectores; actualización por **Bellman‑Ford** hop‑by‑hop; invalidación de rutas ante timeout de vecino.
- **Compatibilidad de headers:** si llega `headers` como lista/dict, se normaliza. Se sincroniza `ttl`↔`hops` y se infiere `alg` cuando falta (info→`lsr`).

---

## 🧯 Troubleshooting

- **`max number of clients reached`**  
  El Redis del lab está lleno. Usa **Redis local** y cambia `--redis-host/--redis-port` a tu IP/puerto local.

- **No se entrega `send` en LSR/DVR**  
  Verifica que exista **ruta** (`show routes`). Si no hay `next-hop`, el nodo no envía unicast (diseño). Espera convergencia o revisa la topología.

- **Zsh: `--transport` “no se reconoce”**  
  Sucede por partir el comando en varias líneas con `\` mal puesto. Usa **una sola línea**.

- **Paths de topo/names**  
  Usa rutas absolutas si aparece `FileNotFoundError`.

---

## 📜 Licencia y créditos

Trabajo académico del curso de Redes (Sección 10). Uso educativo. Creditos a los integrantes del equipo y a los grupos con quienes se realizaron pruebas de interoperabilidad.

---

## ✅ Checklist rápido para demos

- [ ] `names-redis.json` con canales reales `sec10.…`  
- [ ] `topo-*.json` consistente en todos los nodos  
- [ ] Redis accesible (lab o local)  
- [ ] Lanzar todos los nodos (una terminal por nodo)  
- [ ] Esperar 3–5 s de warmup  
- [ ] `show neighbors`, `show routes`  
- [ ] `send <dst> "mensaje"` y captura de evidencia

¡Listo para demo! 🚀

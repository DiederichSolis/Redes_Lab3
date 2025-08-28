# Usa zsh (cámbialo a /bin/bash si prefieres)
SHELL := /bin/zsh

VENV := .venv

.PHONY: install enter activate run-redis run-udp clean

# Crea el venv si no existe
$(VENV)/bin/python:
	python3 -m venv $(VENV)

# Instala deps DENTRO del venv (sin "source")
install: $(VENV)/bin/python requirements.txt
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -r requirements.txt

# Abre una shell interactiva con el venv activado (te deja en (.venv))
enter: install
	@echo "Abriendo shell con entorno activado. Sal para volver (Ctrl-D o 'exit')."
	@. $(VENV)/bin/activate; exec $$SHELL -i

# Solo imprime cómo activar manualmente (por si lo quieres)
activate:
	@echo "Ejecuta: source $(VENV)/bin/activate"

# Ejecuta demo Redis usando el venv (sin activar manualmente)
run-redis: install
	$(VENV)/bin/python run_demo.py \
		--names names-redis.json \
		--topo topo-redis.json \
		--transport redis \
		--algo flooding \
		--src A \
		--dst B \
		--text "Hola desde Makefile" \
		--redis-host lab3.redesuvg.cloud \
		--redis-port 6379 \
		--redis-username default \
		--redis-password UVGRedis2025

# Ejecuta demo UDP como ejemplo
run-udp: install
	$(VENV)/bin/python run_demo.py \
		--names names-sample.json \
		--topo topo-sample.json \
		--transport udp \
		--algo flooding \
		--src A \
		--dst B \
		--text "Hola desde UDP"

# Limpieza
clean:
	rm -rf $(VENV)
	find . -type d -name "__pycache__" -exec rm -r {} +

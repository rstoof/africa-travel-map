PY ?= .venv/bin/python -W ignore

.PHONY: all ref fetch trace build preview serve

all: ref fetch trace build

ref:
	$(PY) src/ref.py

fetch:
	$(PY) src/fetch.py

trace:
	$(PY) src/trace.py

build:
	$(PY) src/build.py

preview:
	$(PY) src/preview.py data/preview.png

serve:
	cd web && python3 -m http.server 8000

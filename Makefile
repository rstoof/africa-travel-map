PY ?= .venv/bin/python -W ignore

SNAPSHOT_DATE ?=

.PHONY: all ref fetch trace build history preview serve

all: ref fetch trace build history

ref:
	$(PY) src/ref.py

fetch:
	$(PY) src/fetch.py

trace:
	$(PY) src/trace.py

build:
	$(PY) src/build.py

history:
	$(PY) src/history.py record $(if $(SNAPSHOT_DATE),--date $(SNAPSHOT_DATE))

preview:
	$(PY) src/preview.py data/preview.png

serve:
	cd web && python3 -m http.server 8000

.PHONY: install township analyze demo serve web test typecheck validate all

install:
	pip install -e ".[dev]"

township:
	python scripts/build_township.py --seed 42 --out data/township.json

analyze:
	python scripts/run_analysis.py --township data/township.json --out results/ --jobs 6

demo:
	python scripts/precompute_demo.py --township data/township.json --out results/

serve:
	uvicorn civictwin.api.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	pytest -q

typecheck:
	mypy civictwin/engine civictwin/analysis

validate:
	python scripts/verify_models.py --township data/township.json --results results/ --out results/validation.json --jobs 6

all: township analyze demo validate

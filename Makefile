# Perintah pengembangan SahamBuddy (jalankan: make <target>)
.PHONY: install test coverage lint compile run docker

install:
	pip install -r requirements-dev.txt

test:
	pytest

coverage:
	pytest --cov=core --cov=handlers --cov=bot --cov=config --cov-report=term-missing --cov-fail-under=75

lint:
	ruff check .

compile:
	python -m compileall -q bot.py config.py core handlers tools research backtest.py backtest_real.py

run:
	python bot.py

docker:
	docker build --build-arg SKIP_MODEL=1 -t saham-bot:slim .

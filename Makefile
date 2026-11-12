# Perintah pengembangan SahamBuddy (jalankan: make <target>)
.PHONY: install test lint compile run docker

install:
	pip install -r requirements-dev.txt

test:
	pytest

lint:
	ruff check .

compile:
	python -m compileall -q bot.py core backtest.py backtest_real.py

run:
	python bot.py

docker:
	docker build --build-arg SKIP_MODEL=1 -t saham-bot:slim .

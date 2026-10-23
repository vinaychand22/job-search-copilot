.PHONY: setup test demo dashboard sync match report eval-tracker eval-matcher eval-matcher-offline coach eval-coach

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	python -m pytest -q

demo:
	python -m copilot.demo
	COPILOT_DB=data/demo.db streamlit run app.py

dashboard:
	streamlit run app.py

sync:
	python -m copilot.tracker.run

match:
	python -m copilot.matcher.run inbox/postings

report:
	python -m copilot.impact report

eval-tracker:
	python -m copilot.tracker.eval

eval-matcher:
	python -m copilot.matcher.eval

eval-matcher-offline:
	python -m copilot.matcher.eval --offline

coach:
	python -m copilot.coach.run --hours 8

eval-coach:
	python -m copilot.coach.eval

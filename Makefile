SOURCE_DIRS := kusss_to_ics

.PHONY: format lint

format:
	ruff format $(SOURCE_DIRS)

lint:
	ruff check $(SOURCE_DIRS) --fix
	pyright .

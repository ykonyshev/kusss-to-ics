# KUSSS to ICS

A CLI tool to crawl appointments from the [Johannes Kepler's University Linz](https://www.jku.at) study administration platform: [Kepler University Study Support System](https://www.kusss.jku.at) (KUSSS) and export into `.ics` files to avoid the manual labour involved each upcoming semester.

The project is a minimal implementation of the required functionality, more things may be added in the future if the time permits. For more information about the known issues and planned features see the `FIXME` and `TODO` comments scattered throughout the source code.

## How to use

> **Note**: The instructions are provided for POXIS-compliant systems.

1. Have Python version >=3.14 installed on your system.
1. Install the project dependencies `uv sync`. You must have [`uv`](https://docs.astral.sh/uv/) installed on your system.
1. Activate the virtual environment `source .venv/bin/activate` (on `bash`-compatible shells) or `source .venv/bin/activate.fish` in [`fish`](https://fishshell.com/).
1. Create a confguration file specifying the information about the curricula that you are interested in, the [`example.toml`](config/example.toml) can be used as reference.
1. Invoke the CLI tool, more information about the possible arguments can be found in the help message.
    - To get the help message: `python -m kusss_to_ics --help`.
    - Example invocation: `python -m kusss_to_ics config/main.toml exports -s 3 --split-by-course-type --primary-program-code="033/536" --additional "Programmierung 1"`.

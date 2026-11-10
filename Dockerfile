FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-group prep

COPY app.py context_panel.py context-panel.js index.html ./
COPY static/ static/
COPY data/ data/
COPY stories/ stories/

ENV PORT=8080
CMD ["uv", "run", "--no-sync", "app.py"]

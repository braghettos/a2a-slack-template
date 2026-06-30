FROM python:3.12-slim

WORKDIR /app

# Match pyproject's requires-python (>=3.12) with the base image, and pin uv to the
# image's system interpreter — otherwise uv downloads a newer managed Python (3.14),
# for which aiohttp has no wheel, forcing a from-source build that needs a C compiler
# the slim image doesn't ship.
ENV UV_PYTHON_PREFERENCE=only-system

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir uv && \
    uv sync --no-dev --frozen

COPY main.py handlers.py ./

ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "main.py"]

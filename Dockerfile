FROM ghcr.io/astral-sh/uv:0.6.14-python3.13-bookworm-slim@sha256:73c021c3fe7264924877039e8a449ad3bb380ec89214282301affa9b2f863c5d AS build

WORKDIR /opt/apg
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY apg ./apg
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/apg/.venv/bin:$PATH

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin apg

WORKDIR /opt/apg
COPY --from=build /opt/apg/.venv ./.venv

USER apg
WORKDIR /work
CMD ["apg", "--help"]

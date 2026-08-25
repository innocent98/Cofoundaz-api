# SOP — Fix: API container could not reach Postgres/Redis (signup 500)

> **Type:** infra / config fix · **Date:** 2026-08-25 · **Area:** docker-compose, env config

## What shipped

Gave the `api` service in `docker-compose.yml` an `environment:` block that points
`DATABASE_URL` / `REDIS_URL` at the compose **service names** (`db:5432`, `redis:6379`), and
realigned the host-side ports in `.env` / `.env.example` (`5433→5432`, `6378→6379`). `POST
/api/v1/auth/signup` now returns `201` from the container instead of a `500`.

## Why (root cause)

`POST /api/v1/auth/signup` returned `500` with
`psycopg2.OperationalError: connection to server at "localhost" (…), port 5433 failed:
Connection refused`.

The API runs inside the `cofoundaz-api_api` container and loaded its DB/Redis URLs from `.env`,
which used **`localhost:5433`** / **`localhost:6378`**. Two problems compounded:

1. **Wrong host for a container.** Inside a container, `localhost` is the container itself — not
   the sibling `db`/`redis` services. The API can only reach them by their compose service DNS
   names (`db`, `redis`) on their **internal** ports (`5432`, `6379`).
2. **Stale ports.** Commit `e87e377` changed the *published host ports* (`5433→5432`,
   `6378→6379`) but `.env` still referenced the old `5433`/`6378`, so even host-side tooling
   would have failed.

Confirmed by probing from inside the container: `db:5432` and `redis:6379` were reachable while
`localhost:5433` / `localhost:6378` were refused.

## How (approach + the container-vs-host split)

The two run contexts need different addresses, so each lives in its correct layer:

- **Container** (compose): `docker-compose.yml` → `api.environment` overrides `DATABASE_URL` /
  `REDIS_URL` to `db:5432` / `redis:6379`. Compose `environment:` takes precedence over
  `env_file:`, so the container is correct regardless of `.env`.
- **Host** (`make run` / alembic / pytest on the machine): `.env` keeps `localhost:<published
  port>` — now `5432` / `6379` to match what compose publishes.

`.env.example` documents the split so the next developer doesn't repeat it. Rejected: pointing
`.env` itself at `db:5432` — that breaks host-side tooling, which can't resolve the service name.

## What's involved

| File | Change |
|---|---|
| `docker-compose.yml` | new `api.environment` with `DATABASE_URL=…@db:5432/…`, `REDIS_URL=redis://redis:6379/0` |
| `.env` (local, gitignored) | host-side ports `5433→5432`, `6378→6379` (DATABASE_URL, REDIS_URL, TEST_DATABASE_URL) |
| `.env.example` | comment explaining host-vs-container addressing |

## Verification

- From inside the container: `db:5432` / `redis:6379` reachable; `localhost:5433` / `:6378` refused.
- After `docker compose up -d api`: `printenv DATABASE_URL` → `…@db:5432/…`.
- `POST /api/v1/auth/signup` → **`201`** with the standard envelope (was `500`).

## Operate / roll back

- Apply: `docker compose up -d api` (recreates the container with the new env).
- Roll back: revert the `docker-compose.yml` `environment:` block.

## Follow-ups (non-blocking)

- Consider a `db` healthcheck + `depends_on: condition: service_healthy` so the API waits for
  Postgres to accept connections on cold `docker compose up` (avoids a startup race).
- If a host Postgres already occupies `5432`, republish the compose db on a free host port and
  update `.env` host-side URLs to match (the container override is unaffected).

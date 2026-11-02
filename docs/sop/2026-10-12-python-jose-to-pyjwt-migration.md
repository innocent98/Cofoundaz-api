# SOP: python-jose → PyJWT migration

**What shipped:** access tokens are now encoded and verified with **PyJWT 2.15.1** instead of
python-jose 3.5.0. This removes `python-jose`, `ecdsa`, `rsa` and `pyasn1` from the dependency tree
and clears **both** pip-audit suppressions, so `.github/security/pip-audit-ignores.txt` is now
empty. Branch `chore/python-jose-to-pyjwt` → `develop`. It is the real fix promised in
`docs/sop/2026-10-12-python-jose-cve-suppression.md` (PR #120).

## Why

| Advisory | Package | Fix available? | Was |
|---|---|---|---|
| `CVE-2026-85394`: HS256 forgery, Critical | python-jose 3.5.0 (its last release) | No | suppressed 2026-10-10 (PR #120), review date 2026-11-10 |
| `PYSEC-2026-1325`: Minerva timing attack | ecdsa 0.19.2 (via python-jose) | No | suppressed 2026-08-27 (PR #18), review date 2026-11-27 |

Neither advisory was reachable on our config: HS256 only, and both decode sites pin the algorithm.
But "suppressed with justification" is a debt with a due date, and the only exit was dropping
python-jose.

## How

Five call sites changed, and nothing else in `app/` did:

| File | Before | After |
|---|---|---|
| `app/core/security.py` | `from jose import jwt` | `import jwt` (`jwt.encode` has the same signature) |
| `app/api/deps.py` | `from jose import JWTError, jwt`; `except (JWTError, ValueError)` | `import jwt`; `except (jwt.PyJWTError, ValueError)` |
| `app/core/rate_limit.py` | `from jose import JWTError, jwt`; `except JWTError` | `import jwt`; `except jwt.PyJWTError` |
| `pyproject.toml` | `python-jose[cryptography] ^3.3.0` | `pyjwt ^2.15.1` (declared directly) |
| `poetry.lock` | — | removes python-jose, ecdsa, rsa, pyasn1. **Nothing added or upgraded.** |

**Key decisions:**
- **The "string `sub`" gotcha was already handled.** `create_access_token` already encodes
  `"sub": str(subject)` (`app/core/security.py:74`), and `get_current_user` parses it back with
  `uuid.UUID`. The pre-scoping worry (PyJWT ≥2.10 rejects a non-string `sub`) didn't apply.
- **No new package.** PyJWT was already installed, transitively via `redis`. It's now declared
  directly so it can't vanish if redis drops it.
- **`cryptography` stays.** It came in with python-jose's extra, but it's also a direct
  dependency (`^50.0.0`), and MFA and journal encryption need it for Fernet. Checked before
  removing python-jose.
- **Expiry behaviour is unchanged.** Both libraries validate `exp` by default.
- **Short-key strictness is not turned on.** PyJWT emits `InsecureKeyLengthWarning` for an HS256
  key under 32 bytes, but only raises if `enforce_minimum_key_length` is set, which we don't do.
  Our documented key (`openssl rand -hex 32`, in `.env.example`) is 64 bytes.

**Rejected:**
- Bundling this with feature work: it's auth-critical, so it got its own PR, a contract test
  suite and a security review.
- Wrapping the JWT calls in an adapter module: three call sites don't justify an abstraction.

## Verification

**Security review (Opus)** found no blocking issues. It fuzzed `jwt.decode` with 30 hostile
inputs (bad base64, 5MB token, non-dict payload, 100k-deep JSON, `exp` NaN/inf/1e400, `alg`/`kid`/`crit`
tricks) and every one raised a `jwt.PyJWTError` subclass, so both `except` clauses catch everything and
none can become a 500. **Incidental fix:** by source reading, under python-jose a validly signed token
with a non-string `sub` reached `uuid.UUID(123)` → uncaught `AttributeError` → 500. PyJWT raises
`InvalidSubjectError` → 401. That couldn't be shown failing locally because python-jose is no longer
installed. Other decode differences (a future `iat` and a present `aud` are now rejected) don't affect
us, since we never issue those claims. As before, a correctly signed token without `exp` never
expires, but only a holder of `SECRET_KEY` can mint one.

**Test-first.** `tests/core/test_jwt_contract.py` (13 tests, 15 after review) was committed **before** the swap.
Its tokens are hand-built with the standard library only (`hmac` + `base64`, RFC 7515 wire format),
so they don't depend on either JWT library.

| Test | python-jose (before) | PyJWT (after) |
|---|---|---|
| Independently built HS256 token authenticates. Proves tokens issued before the deploy still work, so **no forced logout**. | pass | pass |
| Issued token is standard HS256 with a string `sub` and an integer `exp`, and its signature verifies with plain HMAC | pass | pass |
| Expired / tampered payload / wrong secret / `alg: none` / HS512 with the right secret / missing `sub` / non-UUID `sub` / garbage → **401** | pass (8) | pass (8) |
| Rate-limit key falls back to IP for an expired or garbage token | pass (2) | pass (2) |
| `app/` does not import `jose` | **fail** (RED, named the 3 files) | pass |
| *Added after review:* RS256 header with an HMAC signature (algorithm confusion) → 401 | not run | pass |
| *Added after review:* validly signed token with `sub: 123` → 401 | not run (see below) | pass |

Local CI checks, using the project toolchain with Black 26.5.1 matching the lock: black, isort,
ruff, mypy, bandit and pylint (9.88) all pass. The full unit suite is **2153 passed, 97.41% coverage** (floor 95), and the
full e2e suite (`scripts/e2e_run.sh`, every authenticated journey through the new decode path) is
**66/66 passed**, with no `InsecureKeyLengthWarning` and no tracebacks in the server log. pip-audit, run with CI's exact loop, reports **"Suppressed advisories: none"**
and **"No known vulnerabilities found"**.

## Operate / roll back

- **Deploy:** no environment or config change. Sessions survive the deploy, as proven by the
  compatibility test above.
- **One-time check (Adebayo):** confirm the staging and production `SECRET_KEY` are each ≥ 32 bytes.
  If one is shorter, PyJWT logs an `InsecureKeyLengthWarning` on every token. That's not an outage,
  but it means the key should be rotated.
- **Roll back:** revert the PR. That restores python-jose and **both** ignore entries, and the
  Security gate stays green because the suppressions come back with it.

## Follow-ups

- [ ] Consider `enforce_minimum_key_length=True` once the staging/prod key lengths are confirmed.
      That turns a too-short key into a hard failure instead of a warning.

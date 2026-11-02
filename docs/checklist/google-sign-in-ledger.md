# Google sign-in ledger

Branch `feature/google-sign-in` (off `origin/develop` 219ad18b). No migration (uses `oauth_accounts`).

- [x] Read Calendar OAuth, login/MFA/sessions, maintenance middleware, invites
- [x] `google_oidc` (PKCE, JWKS id_token verification), `google_stub` (real RS256), `google_signin` (flow), routes
- [x] Tests `tests/api/auth/test_google_signin.py` (25) green; black/isort/ruff/mypy clean
- [x] Live e2e `e2e/test_google_sign_in.py` + captures `e2e/_captures/google_sign_in/`
- [x] SOP, checklist entry, FE guide
- [x] Opus review: ticket bound to browser + fragment, takeover clears 2FA, stub limited to development/test, limiter loosened (rest in SOP follow-ups)
- [ ] Push, PR to develop, CI green
- [ ] FE brief PR on `docs/google-sign-in-brief`

Notes
- Local: `tests/api/auth/test_sessions_endpoints.py::test_refresh_reuse_is_401` failed locally; refresh code is untouched by this branch (see PR for CI result).
- Scratch infra: docker `gsi-pg` (55711), `gsi-redis` (56711).

# Env Encryption — cofoundaz-api

How environment files reach the VPS without any secret ever entering git in plaintext.

---

## 1. The filenames, because this is what confuses people

There are four `.env`-shaped names in play and they are **not** interchangeable.

| File | Contains | In git? | Lives where |
|---|---|---|---|
| `.env.staging.enc` | AES ciphertext of the staging config | **YES — committed** | repo |
| `.env.production.enc` | AES ciphertext of the production config | **YES — committed** | repo |
| `.env.staging` | plaintext staging config | never | your machine only |
| `.env.production` | plaintext production config | never | your machine only |
| `.env.key` | the AES key itself | **never** | your machine, `$ENV_ENCRYPTION_KEY`, password manager |
| `.env` | what the stack actually reads | never | **the VPS**, and separately your local dev machine |

Two things follow, and both trip people up:

**The file on the server is `.env`.** Not `.env.production`. `docker-compose.prod.yml`
reads `.env`, and CD writes exactly that. The `.env.staging` / `.env.production` names
exist purely so one laptop can hold both environments at once without them colliding.

**`.env` locally is your DEV file**, used by `docker-compose.yml`. It is a completely
different file from the `.env` on the server that happens to share a name. This is why
`make prod-*` sets `STACK_ENV_FILE=.env.production` — so exercising the production stack
locally never reads or overwrites your dev `.env`.

---

## 2. The flow

```
  .env.production            (you edit this, locally, plaintext)
        |
        |  ./scripts/env.sh encrypt production
        v
  .env.production.enc        (committed to git — ciphertext)
        |
        |  CD: openssl -d, using the ENV_ENCRYPTION_KEY environment secret
        v
  .env                       (on the RUNNER, mode 600, deleted when the job ends)
        |
        |  CD: scp to $DEPLOY_PATH
        v
  $DEPLOY_PATH/.env          (on the VPS — what docker compose reads)
```

`$DEPLOY_PATH` is a **per-GitHub-Environment secret**, so the `staging` environment
resolves to the staging stack directory and `production` to the production one. No server
path appears anywhere in this repository — deliberately. A path written into a doc is a
path that goes stale, which is precisely what happened to the previous hardcoded value.

What this buys:

- Environment config is **version-controlled** — auditable, diffable, revertable.
- A `git clone` is **not sufficient** to recover any secret.
- Deploys are **reproducible**: the environment is a committed artifact, not something
  someone edited on the box at 2am.

The corollary, which matters: **editing `.env` directly on the server is pointless.** The
next deploy overwrites it from the committed ciphertext. To change an environment, edit
locally → re-encrypt → commit → redeploy.

---

## 3. `scripts/env.sh`

```
Usage: ./scripts/env.sh <command> [environment]

  generate-key                    Generate a random AES key
  encrypt <staging|production>    .env.<env>     -> .env.<env>.enc
  decrypt <staging|production>    .env.<env>.enc -> .env.<env>
  verify  <staging|production>    Check the .enc decrypts (writes nothing)
  rotate  <staging|production>    Re-encrypt under a fresh key
  diff                            Which vars differ between envs (values masked)
```

Also available as `make env-generate-key`, `make env-encrypt-staging`,
`make env-decrypt-production`, `make env-verify`, `make env-diff`, and so on — run
`make help` for the full list.

### Key discovery

The key is looked up in three places, in order:

1. `$ENV_ENCRYPTION_KEY` — what CD uses.
2. `.env.key` in the repo root — gitignored; what you'll use day to day.
3. Interactive prompt — the fallback.

Put the key in `.env.key` once and every command just works.

### Crypto

AES-256-CBC with PBKDF2 at 100,000 iterations. Chosen because it is present in every
OpenSSL on every runner, VPS and laptop with zero extra dependencies. The threat being
defended against is "someone has the repository", not a state actor with the key. A
64-hex-character key is 256 bits of entropy, which PBKDF2 at that iteration count is
comfortably sufficient for.

The key is passed to `openssl` on a **file descriptor**, never as an argv `pass:` value,
so it does not appear in `ps` output or shell history.

If you ever need per-person access control (some engineers see staging but not
production), the answer is `sops` + age/KMS, not a second homegrown layer. Nothing
downstream cares how `.env` comes into existence — only that it exists at
`$DEPLOY_PATH/.env` before `docker compose up`.

---

## 4. One-time setup

> Adebayo runs this. It is not automated and it is not done yet — the tooling ships
> ready, the secrets do not exist.

```bash
# 1. Generate the key
./scripts/env.sh generate-key          # prints a 64-hex-char key

# 2. Save it locally (gitignored)
echo '<paste-the-key>' > .env.key

# 3. Build the plaintext environment files
cp .env.production.example .env.staging
cp .env.production.example .env.production
$EDITOR .env.staging .env.production   # fill in every CHANGE_ME

# 4. Encrypt both
./scripts/env.sh encrypt staging
./scripts/env.sh encrypt production

# 5. Commit ONLY the ciphertext
git add .env.staging.enc .env.production.enc
git commit -m "chore(env): add encrypted environment files"
```

Then add the key to GitHub in **both** environments:

**Settings → Environments → `staging` → Secrets → `ENV_ENCRYPTION_KEY`**
**Settings → Environments → `production` → Secrets → `ENV_ENCRYPTION_KEY`**

The same key value in both is fine and is the intended setup — one key encrypts both
files. Two keys would work too; nothing in the pipeline assumes either way.

### Sanity check before you push

```bash
make env-verify        # decrypts both .enc files to /dev/null; writes nothing
```

And confirm git will not betray you:

```bash
git add --dry-run .env .env.staging .env.production .env.key
# -> all four must report "ignored by one of your .gitignore files"

git add --dry-run .env.staging.enc .env.production.enc
# -> both must report "add '...'"
```

---

## 5. Day-to-day

**Change a value in staging:**

```bash
./scripts/env.sh decrypt staging     # -> .env.staging (mode 600)
$EDITOR .env.staging
./scripts/env.sh encrypt staging     # -> .env.staging.enc
git add .env.staging.enc && git commit -m "chore(env): update staging env"
```

Push, and CD ships it on the next deploy.

**Compare environments** (values are masked, so this is safe to run on a call):

```bash
./scripts/env.sh decrypt staging
./scripts/env.sh decrypt production
make env-diff
```

**Verify ciphertext is intact without writing plaintext:**

```bash
./scripts/env.sh verify production
```

---

## 6. Rotating the key

Rotate if the key is exposed, when someone with access leaves, or on whatever schedule
you set.

```bash
./scripts/env.sh rotate staging
```

It decrypts with the current key, re-encrypts under a fresh one, and prints the new key
plus the exact list of places to update. **Follow that list before you push**, or CD will
fail to decrypt and every deploy will stop:

1. GitHub → Environments → `staging` → `ENV_ENCRYPTION_KEY`
2. GitHub → Environments → `production` → `ENV_ENCRYPTION_KEY`
3. Password manager
4. Local `.env.key`

Then re-encrypt the **other** environment under the new key — `rotate` only touches the
one you named, and the other file is still readable only with the old key:

```bash
./scripts/env.sh decrypt production    # with the OLD key still in .env.key
echo '<new-key>' > .env.key
./scripts/env.sh encrypt production
```

`rotate` prints these commands for you.

> **Rotating `ENV_ENCRYPTION_KEY` is safe. Rotating `MFA_ENCRYPTION_KEY` is not.**
> They are different keys with very different consequences. `ENV_ENCRYPTION_KEY` only
> protects the file; re-encrypt and everything continues. `MFA_ENCRYPTION_KEY` encrypts
> every stored TOTP secret in the database — change it and every MFA-enrolled user is
> permanently locked out. See `.env.production.example`.

---

## 7. Where the key must live

Exactly three places:

1. **Password manager** — the recovery copy. Lose every other copy and the committed
   `.enc` files are unrecoverable ciphertext.
2. **GitHub environment secrets** — `staging` and `production`.
3. **Local `.env.key`** — gitignored.

Not in Slack, not in email, not in a notes app, not in a `passphrase.txt` next to the
`.enc` files.

---

## 8. If it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Decryption failed: wrong key or corrupted file` | Key mismatch, or the `.enc` was committed from a different key | `./scripts/env.sh verify <env>` with each key you hold |
| CD: `Failed to decrypt .env.<env>.enc` | `ENV_ENCRYPTION_KEY` in that GitHub Environment does not match the committed ciphertext | Re-add the secret; confirm you set it on the right environment |
| CD: `.env.<env>.enc not found` | Never encrypted, or the `.enc` was gitignored | Run `encrypt`, then `git add --dry-run` to confirm it is addable |
| CD: `refusing to deploy a truncated environment` | Decrypt produced fewer than 5 variables | The `.enc` is damaged — re-encrypt from plaintext |
| Decrypt "works" but values look wrong | Trailing newline in `.env.key` vs the GitHub secret | `env.sh` strips it on read; check the GitHub secret has no trailing whitespace |
| Local `make prod-up` picks up dev values | `STACK_ENV_FILE` not set | Use `make prod-*`, which sets it — don't call `docker compose -f docker-compose.prod.yml` by hand |

**Manual decrypt**, for debugging only — this is what CD does under the hood:

```bash
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 \
  -in .env.production.enc -out .env.production \
  -pass "pass:$ENV_ENCRYPTION_KEY"
```

---

## 9. Verification status

Verified locally on 2026-08-28, against **fake data in a throwaway directory** — no real
secret was created, encrypted, or committed:

| Check | Result |
|---|---|
| encrypt → decrypt round-trip | byte-identical; output mode `600` |
| ciphertext | begins `Salted__`; plaintext absent from the file |
| key discovery tier 1 (`$ENV_ENCRYPTION_KEY`) | works |
| key discovery tier 2 (`.env.key`) | works |
| wrong key on `verify` | exits **1**, clear message |
| wrong key on `decrypt` | exits **1**, leaves **no** partial file |
| `rotate` | old key stops working, new key works |
| `diff` | reports differing keys, **masks values** |
| `CHANGE_ME` guard | warns before encrypting a template |
| `shellcheck -S warning` | clean |

**NOT VERIFIED:** no `.enc` file has been produced from real configuration, no
`ENV_ENCRYPTION_KEY` exists in GitHub yet, and the CD decrypt-and-ship path has never run
against a real VPS. That requires the one-time setup in §4 plus a staging host.

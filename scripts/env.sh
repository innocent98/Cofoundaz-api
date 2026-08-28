#!/usr/bin/env bash
# ============================================================
# cofoundaz-api .env encryption helper
#
# Manages encrypted environment files, one per environment:
#   .env.staging      <->  .env.staging.enc
#   .env.production   <->  .env.production.enc
#
# THE FILENAME ON THE SERVER IS `.env`, NOT `.env.<env>`.
# The `.env.staging` / `.env.production` names exist only on your machine, so
# you can hold both environments at once without them colliding. CD decrypts
# `.env.<env>.enc` on the runner, writes it out as `.env`, and scp's THAT to
# $DEPLOY_PATH on the VPS. docker-compose.prod.yml reads `.env`.
#
# Usage:
#   ./scripts/env.sh generate-key           # make a new AES key
#   ./scripts/env.sh encrypt staging        # .env.staging -> .env.staging.enc
#   ./scripts/env.sh decrypt production     # .env.production.enc -> .env.production
#   ./scripts/env.sh verify staging         # can it be decrypted? (writes nothing)
#   ./scripts/env.sh rotate staging         # re-encrypt under a fresh key
#   ./scripts/env.sh diff                   # which vars differ between envs
#
# Key discovery, in order:
#   1. $ENV_ENCRYPTION_KEY environment variable
#   2. .env.key file in the repo root (gitignored)
#   3. Interactive prompt
#
# Both environments intentionally share one key: the thing being defended
# against is "someone cloned the repo", not "staging ops must not read prod".
# If you ever need that separation, use two keys and two GitHub secrets - the
# rest of the pipeline does not care.
#
# Crypto: AES-256-CBC with PBKDF2, 100000 iterations. Chosen because it is in
# every OpenSSL on every runner, VPS and laptop with zero extra dependencies.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
KEY_FILE="$PROJECT_ROOT/.env.key"

PBKDF2_ITER=100000

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

VALID_ENVS=("staging" "production")

validate_env() {
    local env="$1"
    for valid in "${VALID_ENVS[@]}"; do
        [ "$env" = "$valid" ] && return 0
    done
    echo -e "${RED}Error: invalid environment '$env'. Use: staging | production${NC}" >&2
    exit 1
}

env_file() { echo "$PROJECT_ROOT/.env.$1"; }
enc_file() { echo "$PROJECT_ROOT/.env.$1.enc"; }

get_key() {
    if [ -n "${ENV_ENCRYPTION_KEY:-}" ]; then
        printf '%s' "$ENV_ENCRYPTION_KEY"
        return
    fi
    if [ -f "$KEY_FILE" ]; then
        # tr -d strips the trailing newline `echo 'key' > .env.key` leaves behind.
        # Without this the key differs by one byte from $ENV_ENCRYPTION_KEY and
        # decryption fails with a maddeningly unhelpful "bad decrypt".
        tr -d '\r\n' < "$KEY_FILE"
        return
    fi
    echo -e "${YELLOW}No ENV_ENCRYPTION_KEY set and no .env.key file.${NC}" >&2
    read -rsp "Enter encryption key: " key
    echo >&2
    printf '%s' "$key"
}

require_key() {
    local key
    key=$(get_key)
    if [ -z "$key" ]; then
        echo -e "${RED}Error: no encryption key provided.${NC}" >&2
        exit 1
    fi
    printf '%s' "$key"
}

# openssl reads the passphrase from a file descriptor rather than argv so the
# key never appears in `ps` output or a shell history file.
_encrypt() {
    local in="$1" out="$2" key="$3"
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter "$PBKDF2_ITER" \
        -in "$in" -out "$out" -pass fd:3 3<<<"$key"
}

_decrypt_to() {
    local in="$1" out="$2" key="$3"
    openssl enc -aes-256-cbc -d -pbkdf2 -iter "$PBKDF2_ITER" \
        -in "$in" -out "$out" -pass fd:3 3<<<"$key" 2>/dev/null
}

_decrypt_stdout() {
    local in="$1" key="$2"
    openssl enc -aes-256-cbc -d -pbkdf2 -iter "$PBKDF2_ITER" \
        -in "$in" -pass fd:3 3<<<"$key" 2>/dev/null
}

warn_placeholders() {
    local file="$1" n
    n=$(grep -c 'CHANGE_ME' "$file" 2>/dev/null || true)
    if [ "${n:-0}" -gt 0 ]; then
        echo -e "${YELLOW}Warning: $n CHANGE_ME placeholder(s) still in $(basename "$file").${NC}" >&2
        echo -e "${YELLOW}         Encrypting a template will deploy a broken environment.${NC}" >&2
    fi
}

cmd_encrypt() {
    local env="$1"
    validate_env "$env"
    local plain enc key
    plain=$(env_file "$env")
    enc=$(enc_file "$env")

    if [ ! -f "$plain" ]; then
        echo -e "${RED}Error: $plain not found.${NC}" >&2
        echo "Create it first, either from the template:" >&2
        echo "  cp .env.production.example .env.$env   # then edit it" >&2
        echo "or by pulling what is already on the server:" >&2
        echo "  scp -P <PORT> <USER>@<HOST>:<DEPLOY_PATH>/.env .env.$env" >&2
        exit 1
    fi

    warn_placeholders "$plain"
    key=$(require_key)
    _encrypt "$plain" "$enc" "$key"

    echo -e "${GREEN}Encrypted:${NC} .env.$env -> .env.$env.enc"
    echo "  Ciphertext: $(wc -c < "$enc" | tr -d ' ') bytes"
    echo "  Variables:  $(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' "$plain" || true)"
    echo ""
    echo -e "${YELLOW}Next:${NC}"
    echo "  git add .env.$env.enc"
    echo "  git commit -m 'chore(env): update encrypted $env environment'"
}

cmd_decrypt() {
    local env="$1"
    validate_env "$env"
    local plain enc key
    plain=$(env_file "$env")
    enc=$(enc_file "$env")

    [ ! -f "$enc" ] && { echo -e "${RED}Error: $enc not found. Run 'encrypt $env' or pull from git.${NC}" >&2; exit 1; }

    if [ -f "$plain" ]; then
        echo -e "${YELLOW}.env.$env already exists. Overwrite? (y/N)${NC}"
        read -r confirm
        case "$confirm" in
            y|Y) ;;
            *) echo "Aborted."; exit 0 ;;
        esac
    fi

    key=$(require_key)
    if ! _decrypt_to "$enc" "$plain" "$key"; then
        # Remove the truncated output so a failed decrypt cannot leave a
        # half-written file that later gets encrypted or deployed.
        rm -f "$plain"
        echo -e "${RED}Decryption failed: wrong key or corrupted file.${NC}" >&2
        exit 1
    fi
    chmod 600 "$plain"

    echo -e "${GREEN}Decrypted:${NC} .env.$env.enc -> .env.$env (mode 600)"
    echo "  Variables: $(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' "$plain" || true)"
}

cmd_verify() {
    local env="$1"
    validate_env "$env"
    local enc key
    enc=$(enc_file "$env")
    [ ! -f "$enc" ] && { echo -e "${RED}Error: $enc not found.${NC}" >&2; exit 1; }

    key=$(require_key)
    # Decrypts to STDOUT only - never writes plaintext to disk, so this is the
    # safe command to run on a shared machine or in CI.
    if _decrypt_stdout "$enc" "$key" | head -1 | grep -q '='; then
        echo -e "${GREEN}Verification passed.${NC} .env.$env.enc decrypts cleanly."
    else
        echo -e "${RED}Verification FAILED.${NC} Wrong key or corrupted file." >&2
        exit 1
    fi
}

cmd_rotate() {
    local env="$1"
    validate_env "$env"
    local enc old_key new_key tmp other
    enc=$(enc_file "$env")
    [ ! -f "$enc" ] && { echo -e "${RED}Error: $enc not found.${NC}" >&2; exit 1; }

    echo -e "${YELLOW}Rotating the encryption key using $env...${NC}"
    old_key=$(require_key)

    tmp="$PROJECT_ROOT/.env.$env.rotate.tmp"
    if ! _decrypt_to "$enc" "$tmp" "$old_key"; then
        rm -f "$tmp"
        echo -e "${RED}Decryption failed with the current key. Nothing changed.${NC}" >&2
        exit 1
    fi

    new_key=$(openssl rand -hex 32)
    _encrypt "$tmp" "$enc" "$new_key"
    rm -f "$tmp"

    echo -e "${GREEN}Re-encrypted .env.$env.enc under a NEW key.${NC}"
    echo ""
    echo -e "${CYAN}New key:${NC} $new_key"
    echo ""
    echo -e "${YELLOW}This key is now the ONLY way to read that file. Update every${NC}"
    echo -e "${YELLOW}location before you push, or CD will fail to decrypt:${NC}"
    echo "  1. GitHub -> Settings -> Environments -> staging   -> ENV_ENCRYPTION_KEY"
    echo "  2. GitHub -> Settings -> Environments -> production -> ENV_ENCRYPTION_KEY"
    echo "  3. Your password manager"
    echo "  4. Local:  echo '$new_key' > .env.key"
    echo ""
    [ "$env" = "staging" ] && other="production" || other="staging"
    echo -e "${YELLOW}Then re-encrypt the OTHER environment under the same new key,${NC}"
    echo -e "${YELLOW}or it will still be readable only with the OLD key:${NC}"
    echo "  ./scripts/env.sh decrypt $other          # with the OLD key still in .env.key"
    echo "  echo '$new_key' > .env.key"
    echo "  ./scripts/env.sh encrypt $other"
}

cmd_diff() {
    local staging production
    staging=$(env_file "staging")
    production=$(env_file "production")

    if [ ! -f "$staging" ] || [ ! -f "$production" ]; then
        echo -e "${YELLOW}Decrypt both environments first:${NC}" >&2
        [ ! -f "$staging" ] && echo "  ./scripts/env.sh decrypt staging" >&2
        [ ! -f "$production" ] && echo "  ./scripts/env.sh decrypt production" >&2
        exit 1
    fi

    echo -e "${CYAN}Variables that DIFFER between staging and production:${NC}"
    echo ""

    local any_diff=false key staging_val production_val
    while IFS= read -r key; do
        [ -z "$key" ] && continue
        staging_val=$(grep "^${key}=" "$staging" 2>/dev/null | head -1 | cut -d= -f2-)
        production_val=$(grep "^${key}=" "$production" 2>/dev/null | head -1 | cut -d= -f2-)
        if [ "$staging_val" != "$production_val" ]; then
            # Values are NOT printed. Half the point of this command is to run it
            # in front of someone else, and these are live credentials.
            echo -e "  ${YELLOW}$key${NC}"
            echo "    staging:    $([ -n "$staging_val" ] && echo '<set>' || echo '(not set)')"
            echo "    production: $([ -n "$production_val" ] && echo '<set>' || echo '(not set)')"
            any_diff=true
        fi
        # Union of variable NAMES across both files. sed rather than grep -oE:
        # a lookahead would need PCRE, which BSD/macOS grep does not have.
    done < <(cat "$staging" "$production" | sed -nE 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' | sort -u)

    if [ "$any_diff" = false ]; then
        echo -e "  ${GREEN}No differences.${NC}"
    fi
    echo ""
    echo -e "${CYAN}(values intentionally masked - use 'diff .env.staging .env.production' locally if you need them)${NC}"
}

cmd_generate_key() {
    local key
    key=$(openssl rand -hex 32)
    echo -e "${GREEN}Generated key:${NC} $key"
    echo ""
    echo "Store it in exactly three places:"
    echo "  1. Your password manager (this is the recovery copy)"
    echo "  2. GitHub -> Settings -> Environments -> staging AND production"
    echo "     -> secret ENV_ENCRYPTION_KEY"
    echo "  3. Locally (gitignored):   echo '$key' > .env.key"
    echo ""
    echo -e "${YELLOW}Not in Slack, not in email, not in a notes app.${NC}"
}

usage() {
    cat <<'USAGE'
Usage: ./scripts/env.sh <command> [environment]

Commands:
  generate-key                    Generate a random AES key
  encrypt <staging|production>    Encrypt .env.<env> -> .env.<env>.enc
  decrypt <staging|production>    Decrypt .env.<env>.enc -> .env.<env>
  verify  <staging|production>    Check the .enc decrypts (writes nothing)
  rotate  <staging|production>    Re-encrypt under a fresh key
  diff                            Show which vars differ between envs (values masked)

Files:
  .env.staging.enc      Encrypted staging config      COMMITTED
  .env.production.enc   Encrypted production config   COMMITTED
  .env.staging          Plaintext staging             gitignored
  .env.production       Plaintext production          gitignored
  .env.key              The AES key                   gitignored
  .env                  What lands on the VPS         gitignored

Key discovery order:
  1. $ENV_ENCRYPTION_KEY
  2. .env.key
  3. Interactive prompt

Docs: docs/deployment/ENV_ENCRYPTION.md
USAGE
}

case "${1:-help}" in
    encrypt)
        [ -z "${2:-}" ] && { echo -e "${RED}Usage: ./scripts/env.sh encrypt <staging|production>${NC}" >&2; exit 1; }
        cmd_encrypt "$2" ;;
    decrypt)
        [ -z "${2:-}" ] && { echo -e "${RED}Usage: ./scripts/env.sh decrypt <staging|production>${NC}" >&2; exit 1; }
        cmd_decrypt "$2" ;;
    verify)
        [ -z "${2:-}" ] && { echo -e "${RED}Usage: ./scripts/env.sh verify <staging|production>${NC}" >&2; exit 1; }
        cmd_verify "$2" ;;
    rotate)
        [ -z "${2:-}" ] && { echo -e "${RED}Usage: ./scripts/env.sh rotate <staging|production>${NC}" >&2; exit 1; }
        cmd_rotate "$2" ;;
    diff) cmd_diff ;;
    generate-key) cmd_generate_key ;;
    help|--help|-h) usage ;;
    *) echo -e "${RED}Unknown command: $1${NC}" >&2; echo ""; usage; exit 1 ;;
esac

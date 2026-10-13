#!/usr/bin/env bash
set -euo pipefail

case ${DEPLOY_BRANCH:-} in
  development) environment=development ;;
  main) environment=production ;;
  *) echo 'Invalid deployment branch' >&2; exit 2 ;;
esac

[[ -n ${DEPLOY_ENV_CONTENT:-} ]] || { echo 'DEPLOY_ENV GitHub Environment secret is missing' >&2; exit 1; }
[[ -n ${NPM_CREDENTIALS_JSON:-} ]] || { echo 'NPM_CREDENTIALS_JSON GitHub Actions secret is missing' >&2; exit 1; }
[[ -n ${EXPECTED_SUPABASE_REF:-} ]] || { echo 'EXPECTED_SUPABASE_REF GitHub Environment secret is missing' >&2; exit 1; }
[[ -n ${DEPLOY_DOMAIN:-} ]] || { echo 'DEPLOY_DOMAIN GitHub Environment secret is missing' >&2; exit 1; }
[[ -n ${DEPLOY_HOST:-} && -n ${DEPLOY_SSH_USER:-} ]] || { echo 'Deployment SSH secrets are missing' >&2; exit 1; }
[[ -s ${RUNNER_TEMP:-}/deploy_known_hosts ]] || { echo 'Deployment SSH host key is missing' >&2; exit 1; }

umask 077
stage=$(mktemp -d)
trap 'rm -f "$stage/deploy.env" "$stage/npm-credentials.json"; rmdir "$stage"' EXIT
printf '%s\n' "$DEPLOY_ENV_CONTENT" > "$stage/deploy.env"
printf '\nDEPLOY_DOMAIN=%s\n' "$DEPLOY_DOMAIN" >> "$stage/deploy.env"
printf '%s\n' "$NPM_CREDENTIALS_JSON" > "$stage/npm-credentials.json"
unset DEPLOY_ENV_CONTENT NPM_CREDENTIALS_JSON

python3 "$(dirname "$0")/check_env.py" "$stage/deploy.env"
python3 - "$stage/npm-credentials.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as credentials_file:
    credentials = json.load(credentials_file)
if not isinstance(credentials, dict) or not all(
    isinstance(credentials.get(key), str) and credentials[key]
    for key in ("identity", "secret")
):
    raise SystemExit("NPM credentials need identity and secret")
PY

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$RUNNER_TEMP/deploy_known_hosts")
remote="$DEPLOY_SSH_USER@$DEPLOY_HOST"
root=deploy/iinfo-dx-backend
ssh "${ssh_opts[@]}" "$remote" 'umask 077; mkdir -p "$HOME/deploy/iinfo-dx-backend/env"'
scp "${ssh_opts[@]}" "$stage/deploy.env" "$remote:$root/env/.$environment.env.new"
scp "${ssh_opts[@]}" "$stage/npm-credentials.json" "$remote:$root/.npm-credentials.json.new"
ssh "${ssh_opts[@]}" "$remote" \
  "root=\"\$HOME/$root\"; chmod 600 \"\$root/env/.$environment.env.new\" \"\$root/.npm-credentials.json.new\" && \
   mv -f \"\$root/env/.$environment.env.new\" \"\$root/env/$environment.env\" && \
   mv -f \"\$root/.npm-credentials.json.new\" \"\$root/npm-credentials.json\""

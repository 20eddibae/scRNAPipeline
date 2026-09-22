#!/usr/bin/env bash
# .git/hooks is not tracked and does not survive a clone. Re-run this after cloning.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hook="$root/.git/hooks/pre-push"

cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
# Refuses a push whose outbound commits contain anything credential-shaped.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "0000000000000000000000000000000000000000" ] && continue
  if ! python3 "$root/scripts/scan_secrets.py" --outbound "$remote_sha"; then
    status=1
  fi
done
exit $status
HOOK

chmod +x "$hook"
echo "installed $hook"

#!/usr/bin/env bash
# list-ingress-host-paths.sh
# Lists all Ingress host+path entries across all namespaces.
# Usage: bash scripts/list-ingress-host-paths.sh [--namespace <ns>]

set -euo pipefail

NAMESPACE="${1:-}"

if [[ -n "$NAMESPACE" ]]; then
  NS_FLAG="-n $NAMESPACE"
else
  NS_FLAG="--all-namespaces"
fi

kubectl get ingress $NS_FLAG -o json \
  | python3 - <<'EOF'
import json, sys

data = json.load(sys.stdin)
for ing in data.get("items", []):
    ns   = ing["metadata"]["namespace"]
    name = ing["metadata"]["name"]
    tls_hosts = set()
    for tls in (ing.get("spec") or {}).get("tls") or []:
        tls_hosts.update(tls.get("hosts") or [])
    for rule in (ing.get("spec") or {}).get("rules") or []:
        host = rule.get("host", "")
        http = rule.get("http") or {}
        for p in http.get("paths") or []:
            path   = p.get("path", "/")
            scheme = "https" if host in tls_hosts else "http"
            print(f"{ns}\t{name}\t{scheme}://{host}{path}")
EOF

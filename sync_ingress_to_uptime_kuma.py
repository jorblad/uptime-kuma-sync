#!/usr/bin/env python3
"""
Sync Kubernetes Ingress host+path -> Uptime Kuma monitors.

Environment variables:
  UPTIME_KUMA_BASE       e.g. https://uptime-kuma.example.com
  UPTIME_KUMA_API_TOKEN  Bearer token for Uptime Kuma API

Dependencies: kubernetes, requests
"""

import os
import json
import logging
from typing import Dict, List
import requests
from kubernetes import client, config

# -------- CONFIG (edit defaults via env) --------
UPTIME_KUMA_BASE = os.getenv("UPTIME_KUMA_BASE", "")
UPTIME_KUMA_API_TOKEN = os.getenv("UPTIME_KUMA_API_TOKEN", "")
ANNOTATION_KEY = "uptime-kuma/monitors"
DEFAULT_MONITOR = {
    "type": "http",
    "interval": 60,
    "tags": ["k8s-ingress"]
}
# End config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync")

if not UPTIME_KUMA_BASE:
    logger.error("UPTIME_KUMA_BASE is not set")
    raise SystemExit(1)

def auth_headers():
    if not UPTIME_KUMA_API_TOKEN:
        raise RuntimeError("Set UPTIME_KUMA_API_TOKEN")
    return {"Authorization": f"Bearer {UPTIME_KUMA_API_TOKEN}", "Content-Type": "application/json"}

# Uptime Kuma API adapters (adjust if your version differs)
def list_monitors() -> List[Dict]:
    r = requests.get(f"{UPTIME_KUMA_BASE}/monitors", headers=auth_headers(), timeout=15)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        logger.error("list_monitors: HTTP error %s: %s", r.status_code, r.text)
        raise
    try:
        return r.json()
    except Exception as e:
        logger.error("list_monitors: failed to parse JSON response (status=%s). Response body: %s", r.status_code, r.text)
        raise RuntimeError(f"Failed to parse JSON from Uptime Kuma /monitors: {e}; response={r.text}")

def create_monitor(url: str, name: str, monitor_config: Dict) -> int:
    payload = {"name": name, "url": url, **monitor_config}
    r = requests.post(f"{UPTIME_KUMA_BASE}/monitors/new", headers=auth_headers(), json=payload, timeout=15)
    r.raise_for_status()
    resp = r.json()
    if isinstance(resp, dict) and "id" in resp:
        return int(resp["id"])
    if isinstance(resp, dict) and "monitor" in resp and "id" in resp["monitor"]:
        return int(resp["monitor"]["id"])
    try:
        return int(resp)
    except (ValueError, TypeError):
        raise RuntimeError(f"Unexpected create response: {resp}")

def update_monitor(monitor_id: int, url: str, name: str, monitor_config: Dict) -> None:
    payload = {"name": name, "url": url, **monitor_config}
    r = requests.put(f"{UPTIME_KUMA_BASE}/monitors/{monitor_id}", headers=auth_headers(), json=payload, timeout=15)
    r.raise_for_status()

# Kubernetes helpers
def load_k8s_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.NetworkingV1Api()

def list_ingress_host_paths() -> Dict:
    api = load_k8s_client()
    items = api.list_ingress_for_all_namespaces().items
    out = {}
    for ing in items:
        ns = ing.metadata.namespace
        name = ing.metadata.name
        tls_hosts = set()
        if ing.spec.tls:
            for tls in ing.spec.tls:
                if tls.hosts:
                    tls_hosts.update(tls.hosts)
        rules = ing.spec.rules or []
        for rule in rules:
            host = rule.host or ""
            http = getattr(rule, "http", None)
            if not http:
                continue
            for p in (http.paths or []):
                path = p.path or "/"
                key = (ns, name, f"{host}|{path}")
                out[key] = {"namespace": ns, "ingress_name": name, "host": host, "path": path, "https": (host in tls_hosts)}
    return out

def read_ingress_annotation(api, ns: str, name: str):
    ing = api.read_namespaced_ingress(name=name, namespace=ns)
    ann = ing.metadata.annotations or {}
    return ann.get(ANNOTATION_KEY, "{}")

def patch_ingress_annotation(api, ns: str, name: str, mapping: Dict):
    body = {"metadata": {"annotations": {ANNOTATION_KEY: json.dumps(mapping)}}}
    api.patch_namespaced_ingress(name=name, namespace=ns, body=body)

def reconcile():
    logger.info("Start reconciliation")
    api = load_k8s_client()
    desired = list_ingress_host_paths()
    kuma_monitors = list_monitors()
    monitors_by_url = {}
    for m in kuma_monitors:
        url = m.get("url") or m.get("address") or ""
        monitors_by_url[url] = m

    per_ing = {}
    for (ns, name, hp), info in desired.items():
        per_ing.setdefault((ns, name), {})[f"{info['host']}|{info['path']}"] = info

    for (ns, name), mapping in per_ing.items():
        logger.info(f"Reconciling {ns}/{name}")
        ann_raw = read_ingress_annotation(api, ns, name)
        try:
            ann_map = json.loads(ann_raw) if ann_raw else {}
        except json.JSONDecodeError:
            ann_map = {}
        modified = False

        for hp_key, info in mapping.items():
            host, path = hp_key.split("|", 1)
            if not path.startswith("/"):
                path = "/" + path
            scheme = "https" if info.get("https") else "http"
            url = f"{scheme}://{host}{path}"
            monitor_name = f"{host}{path} (k8s:{ns}/{name})"
            existing_id = ann_map.get(hp_key)
            # try adopt or update
            adopted_id = None
            if existing_id:
                try:
                    mid = int(existing_id)
                    m = next((x for x in kuma_monitors if str(x.get("id") or x.get("_id") or "") == str(mid)), None)
                    if m:
                        curr_url = m.get("url") or m.get("address") or ""
                        if curr_url != url or m.get("name") != monitor_name:
                            logger.info(f"Updating monitor {mid} -> {url}")
                            update_monitor(mid, url, monitor_name, DEFAULT_MONITOR)
                        adopted_id = mid
                except (ValueError, StopIteration):
                    adopted_id = None
            if adopted_id:
                ann_map[hp_key] = str(adopted_id)
                continue
            # adopt by URL if exists
            if url in monitors_by_url:
                mon = monitors_by_url[url]
                mid = int(mon.get("id") or mon.get("_id") or 0)
                logger.info(f"Adopting existing monitor id {mid} for {url}")
            else:
                logger.info(f"Creating monitor for {url}")
                mid = create_monitor(url, monitor_name, DEFAULT_MONITOR)
                kuma_monitors.append({"id": mid, "url": url, "name": monitor_name})
                monitors_by_url[url] = {"id": mid, "url": url, "name": monitor_name}
            ann_map[hp_key] = str(mid)
            modified = True

        if modified:
            patch_ingress_annotation(api, ns, name, ann_map)
            logger.info(f"Patched annotation for {ns}/{name}")

    logger.info("Reconciliation complete")

if __name__ == "__main__":
    reconcile()

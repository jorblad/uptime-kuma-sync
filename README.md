# uptime-kuma-ingress-sync

Sync Kubernetes Ingress host+path to Uptime Kuma monitors.

This repository contains:
- A Python reconciler that creates/updates Uptime Kuma monitors for each Ingress host+path.
- A Dockerfile and GitHub Actions workflow to build/publish the image.
- Kubernetes manifests (CronJob + RBAC) under `k8s/`.
- A small helper script `scripts/list-ingress-host-paths.sh` to list current Ingress host+path entries.

Important: do NOT store real secrets in this repo. Create the `uptime-kuma-secret` locally on your host (outside of git) and apply it to the cluster.

## Quick start
1. Create the repository `jorblad/uptime-kuma-sync` and push these files (see commands below).
2. Edit `.github/workflows/publish.yml` to set your image registry/org and the image name used in `k8s/sync-cronjob.yaml`.
3. Push to `main` (or create a branch and open a PR). The GitHub Actions workflow will build and push the image when configured.
4. Create the Kubernetes Secret locally (example below) and apply it:

```bash
cat > ~/secrets/uptime-kuma-secret.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: uptime-kuma-secret
  namespace: kube-system
type: Opaque
stringData:
  base_url: "https://uptime-kuma.example.com"
  api_token: "REPLACE_WITH_REAL_TOKEN"
EOF
chmod 600 ~/secrets/uptime-kuma-secret.yaml
kubectl apply -f ~/secrets/uptime-kuma-secret.yaml
```

5. Configure ArgoCD app-of-apps to include this repo's `k8s/` manifests (or create an ArgoCD Application in your root repo that points to this repo's `k8s/` folder).

## Verify
- ArgoCD shows the child application (if you add it in your app-of-apps repo).
- `kubectl -n kube-system get cronjob uptime-kuma-ingress-sync`
- Run a test job:
  `kubectl -n kube-system create job --from=cronjob/uptime-kuma-ingress-sync test-sync-$(date +%s)`
- Inspect logs:
  `kubectl -n kube-system logs -l job-name=<job-name> -c sync`

## Notes
- The script stores monitor IDs in the Ingress annotation `uptime-kuma/monitors`.
- Use a pinned image tag in `k8s/sync-cronjob.yaml` (not `:latest`). Update the tag in the manifest when you publish a new image, or use an image-updater.
- You manage the secret locally; this repo only contains a template in `k8s/uptime-kuma-secret.template.yaml`.
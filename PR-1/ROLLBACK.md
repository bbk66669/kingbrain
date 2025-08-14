# PR-1: Orchestrator API Rollback Procedures

This document provides instructions for rolling back the Orchestrator API changes if needed.

## Kubernetes Rollback

To roll back the deployment to the previous version:

```bash
kubectl -n orchestrator rollout undo deploy/kb-orchestrator
```

You can verify the rollback status with:

```bash
kubectl -n orchestrator rollout status deploy/kb-orchestrator
```

And check the pods:

```bash
kubectl -n orchestrator get pods
```

## Git Rollback

To revert the changes in the git repository:

```bash
# Find the commit hash of the PR-1 merge
git log --oneline

# Revert the PR-1 commit
git revert <commit-hash>

# Push the revert commit
git push origin main
```

Alternatively, if you need to revert multiple commits from the PR:

```bash
# Find the commit hash before PR-1 changes
git log --oneline

# Reset to that commit (soft reset to keep changes staged)
git reset <commit-hash-before-pr1>

# Commit the reversion
git commit -m "Revert PR-1: Orchestrator API changes"

# Push the revert commit
git push origin main
```

## Verification After Rollback

After rolling back, verify that the service is still operational:

```bash
# Check the health endpoint
curl http://kb.mwwnd.org/kb-api/health

# Expected response should be the original health check format
```

## Emergency Procedures

If the rollback doesn't restore service functionality:

1. Check the logs:
   ```bash
   kubectl -n orchestrator logs deploy/kb-orchestrator
   ```

2. If necessary, scale down and then up:
   ```bash
   kubectl -n orchestrator scale deploy/kb-orchestrator --replicas=0
   kubectl -n orchestrator scale deploy/kb-orchestrator --replicas=1
   ```

3. If all else fails, restore from the last known good deployment:
   ```bash
   # Apply the previous known good manifest
   kubectl apply -f previous-kb-orchestrator.yaml
   ```

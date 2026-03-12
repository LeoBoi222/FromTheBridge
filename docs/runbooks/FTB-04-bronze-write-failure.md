# FTB-04: Bronze Write Failure Recovery

**Trigger:** MinIO unreachable or partition write fails
**Severity:** Red (MinIO unreachable) / Yellow (partition write failure)

## Detection

Bronze writes happen during `empire_to_forge_sync` (via `src/ftb/writers/bronze.py`). Failures appear in Dagster run logs. `ftb_ops.adapter_health` may not directly detect this — check Dagster run status.

```bash
# Check MinIO connectivity
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/bronze-hot/ 2>&1 | tail -5"

# Check recent Dagster run failures
ssh root@192.168.68.11 "docker exec empire_dagster_webserver dagster run list --limit 10"
```

## Impact

- Bronze is the raw landing zone — data preservation layer
- If Bronze write fails but Silver write succeeds, data is in Silver but raw copy is lost
- If MinIO is completely down, both Bronze and Gold writes fail
- Bronze archive job (`archive_daily_schedule`) will also fail

## Immediate Action

1. **Check MinIO container status**
2. **Check disk space** on `/mnt/empire-data` (MinIO storage)
3. **Do NOT attempt to fix by writing directly** — let the pipeline retry

## Resolution Steps

### MinIO unreachable

```bash
# Check container status
ssh root@192.168.68.11 "docker ps -f name=empire_minio --format '{{.Status}}'"

# Check MinIO logs
ssh root@192.168.68.11 "docker logs empire_minio --tail 50"

# Check disk space (MinIO data lives on /mnt/empire-data)
ssh root@192.168.68.11 "df -h /mnt/empire-data"

# Restart MinIO if container is unhealthy
ssh root@192.168.68.11 "cd /opt/empire/FromTheBridge && docker compose restart empire_minio"

# Verify MinIO is back
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/bronze-hot/ 2>&1 | head -5"
```

### Partition write failure (MinIO is up but write fails)

```bash
# Check MinIO bucket policy
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/bronze-hot/ --recursive 2>&1 | tail -10"

# Check bronze_writer credentials
ssh root@192.168.68.11 "cat /opt/empire/FromTheBridge/secrets/minio_bronze_writer_*"

# Check for lifecycle policy issues (90-day expiry on bronze-hot)
ssh root@192.168.68.11 "docker exec empire_minio mc ilm ls local/bronze-hot/"
```

### After MinIO recovery

Re-trigger the sync to write any missed Bronze partitions:

```bash
# Materialize empire_to_forge_sync from Dagster UI
# Or check if the next scheduled run (every 6h) will cover the gap
```

## Verification

- [ ] `docker ps` shows empire_minio healthy
- [ ] Bronze-hot bucket is writable (test with `mc cp`)
- [ ] Next sync run completes with Bronze write success in logs
- [ ] Bronze archive job runs successfully at 02:00 UTC

## Post-Mortem

Document: failure type (connectivity vs write vs disk), duration, whether Silver was affected, data loss assessment.

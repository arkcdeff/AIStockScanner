# Backup Strategy

## Database Backups

### Automated Daily Backup
```bash
# Add to crontab
0 2 * * * /usr/bin/pg_dump -U postgres aistock > /backups/aistock_$(date +\%Y\%m\%d).sql
```

### Backup Script
```bash
#!/bin/bash
BACKUP_DIR="/backups"
DB_NAME="aistock"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup
pg_dump -U postgres $DB_NAME | gzip > $BACKUP_DIR/aistock_$DATE.sql.gz

# Retain last 30 days
find $BACKUP_DIR -name "aistock_*.sql.gz" -mtime +30 -delete

# Upload to S3 (optional)
aws s3 cp $BACKUP_DIR/aistock_$DATE.sql.gz s3://your-bucket/backups/
```

### Restore
```bash
# Restore from backup
gunzip < aistock_20260101_000000.sql.gz | psql -U postgres aistock
```

## Raw Data Backup
```bash
# Backup NSE raw data
tar -czf nse_raw_$(date +%Y%m%d).tar.gz data/raw/nse/
```

## Configuration Backup
```bash
# Backup environment and configs
tar -czf config_$(date +%Y%m%d).tar.gz .env alembic.ini pyproject.toml
```

## Verification
```bash
# Verify backup integrity
gunzip -t aistock_20260101_000000.sql.gz
```

## Disaster Recovery
1. Restore PostgreSQL from latest backup
2. Run migrations: `uv run alembic upgrade head`
3. Restart application
4. Verify health: `curl http://localhost:8000/health`

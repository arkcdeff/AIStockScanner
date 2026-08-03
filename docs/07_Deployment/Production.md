# Production Deployment

## Architecture
```
Internet → Nginx → Uvicorn → FastAPI → PostgreSQL
```

## Prerequisites
- Ubuntu 22.04+ server
- PostgreSQL 18
- Nginx
- Systemd
- SSL certificate (Let's Encrypt)

## Deployment Steps

### 1. Server Setup
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install PostgreSQL
sudo apt install postgresql-18 postgresql-contrib-18 -y

# Install Nginx
sudo apt install nginx -y

# Install Python
sudo apt install python3.13 python3.13-venv -y
```

### 2. Application Setup
```bash
# Create application directory
sudo mkdir -p /opt/aistock
sudo chown $USER:$USER /opt/aistock
cd /opt/aistock

# Clone repository
git clone https://github.com/arkcdeff/AIStockScanner.git .
```

### 3. Virtual Environment
```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e ".[prod]"
```

### 4. Database Setup
```bash
# Create database
sudo -u postgres psql
CREATE DATABASE aistock;
CREATE USER aistock WITH PASSWORD 'secure-password';
GRANT ALL PRIVILEGES ON DATABASE aistock TO aistock;
\q

# Run migrations
uv run alembic upgrade head
```

### 5. Environment Configuration
```bash
sudo nano /opt/aistock/.env
```

### 6. Systemd Service
```bash
sudo nano /etc/systemd/system/aistock.service
```

```ini
[Unit]
Description=AI Stock Scanner
After=network.target postgresql.service

[Service]
Type=exec
User=www-data
WorkingDirectory=/opt/aistock
Environment="PATH=/opt/aistock/venv/bin"
ExecStart=/opt/aistock/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 7. Nginx Configuration
```bash
sudo nano /etc/nginx/sites-available/aistock
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 8. SSL Certificate
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

### 9. Start Services
```bash
sudo systemctl enable aistock
sudo systemctl start aistock
sudo systemctl enable nginx
sudo systemctl start nginx
```

## Monitoring
- Application logs: `journalctl -u aistock -f`
- Nginx logs: `/var/log/nginx/access.log`
- PostgreSQL logs: `/var/log/postgresql/`

## Scaling
- Increase Uvicorn workers for CPU-bound tasks
- Use Redis for caching scanner results
- Add load balancer for multiple instances

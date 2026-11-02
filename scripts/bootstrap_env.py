"""Generate local-only secrets. This script never prints the generated values."""
from pathlib import Path
import secrets
import os

root=Path(__file__).resolve().parents[1]
p=root/'.env'
if p.exists():raise SystemExit('Existing .env kept unchanged')
content=(f'POSTGRES_DB=paperbot\nPOSTGRES_USER=paperbot\n'
         f'POSTGRES_PASSWORD={secrets.token_urlsafe(48)}\n'
         f'DASHBOARD_TOKEN={secrets.token_urlsafe(48)}\n'
         'FRONTEND_PORT=8097\nNEWS_MODE=mock\nOPENAI_API_KEY=\nOPENAI_MODEL=gpt-5-mini\n')
fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:f.write(content)
print('Local-only .env created (0600). Secrets are intentionally not displayed.')

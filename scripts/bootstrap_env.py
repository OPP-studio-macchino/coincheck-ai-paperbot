"""Create local non-public credentials; never print or overwrite them."""
from pathlib import Path
import os
import secrets
import subprocess

root=Path(__file__).resolve().parents[1]
vault=root/"secrets"
vault.mkdir(mode=0o700,exist_ok=True)
vault.chmod(0o700)
names=(
    "db_admin_password","db_market_password","db_news_password",
    "db_query_password","db_control_password","view_token","admin_token"
)
for name in names:
    dest=vault/name
    if dest.exists():
        if not dest.is_file() or dest.is_symlink():
            raise SystemExit("Invalid existing credential file: "+name)
        continue
    # Compose local secrets are bind-mounted, not Swarm-encrypted secrets.
    # 0444 file inside private 0700 directory for non-root container readability.
    fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o444)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        f.write(secrets.token_urlsafe(48)+"\n")
    dest.chmod(0o444)
env=root/".env"
if not env.exists():
    sha=subprocess.run(["git","rev-parse","HEAD"],cwd=root,
                       capture_output=True,text=True,check=True).stdout.strip()
    lines=[
        "COMPOSE_PROJECT_NAME=coincheck-ai-security",
        "POSTGRES_DB=paperbot",
        "POSTGRES_USER=paper_admin",
        "FRONTEND_PORT=18098",
        "BUY_BUDGET_JPY=50000",
        "INITIAL_CASH_JPY=100000",
        "PAPER_FEE_BPS=0",
        "NEWS_MODE=mock",
        "DRY_RUN=true",
        "LIVE_TRADING_ENABLED=false",
        "APP_GIT_SHA="+sha,
    ]
    fd=os.open(env,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:f.write("\n".join(lines)+"\n")
else:
    if not env.is_file() or env.is_symlink():
        raise SystemExit("Invalid .env; refusing overwrite")
    env.chmod(0o600)
print("Local credentials ready. Values are never printed. Existing files preserved.")

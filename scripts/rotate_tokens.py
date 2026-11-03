"""Rotate view or admin token in private local vault. Restart API to apply mounts."""
from __future__ import annotations
from pathlib import Path
import argparse,os,secrets,tempfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("role",choices=("view","admin"))
args=parser.parse_args()
folder=root/"secrets"
if not folder.is_dir() or folder.is_symlink():
    raise SystemExit("Missing trusted local vault")
p=folder/(args.role+"_token")
if not p.is_file() or p.is_symlink():
    raise SystemExit("Missing token file")
fd,temp=tempfile.mkstemp(prefix=".rotation_",dir=folder)
try:
    os.fchmod(fd,0o444)
    with os.fdopen(fd,"w") as f:
        f.write(secrets.token_urlsafe(48)+"\n")
        f.flush();os.fsync(f.fileno())
    os.replace(temp,p)
finally:
    if os.path.exists(temp):os.unlink(temp)
print("Token rotated locally. Recreate ONLY api container; existing clients must reauthenticate.")

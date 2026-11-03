"""Read-only tracked-file and reachable-history secret scan; never echo secret values."""
from __future__ import annotations
import re, subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PROTECTED=re.compile(r"(^|/)(secrets|runtime|backups|data|results|\.venv)/|(^|/)\.env($|\.)")
PATTERNS=(
    re.compile("sk-"+"proj-"+r"[A-Za-z0-9_-]{20,}"),
    re.compile("sk-"+r"[A-Za-z0-9_-]{40,}"),
    re.compile("-----BEGIN"+" (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)COINCHECK_(?:API_SECRET|SECRET_KEY)\s*=\s*['\"]?[A-Za-z0-9_-]{32,}"),
)

def git_bytes(*args:str)->bytes:
    return subprocess.check_output(["git",*args],cwd=ROOT,stderr=subprocess.DEVNULL)

def is_protected(path:str)->bool:
    return path!=".env.example" and bool(PROTECTED.search(path))

def scan_text(data:bytes)->list[tuple[int,str]]:
    try:content=data.decode("utf-8")
    except UnicodeDecodeError:return []
    matches=[]
    for i,line in enumerate(content.splitlines(),1):
        if any(pattern.search(line) for pattern in PATTERNS):
            matches.append((i,"potential_credential"))
    return matches

def inspect():
    problems=[]
    checked=0
    current=[n for n in git_bytes("ls-files","-z").decode().split("\0") if n]
    for rel in current:
        if is_protected(rel):
            problems.append((rel,0,"protected_file_tracked"))
            continue
        p=ROOT/rel
        if not p.is_file():continue
        if p.stat().st_size>5_000_000:continue
        checked+=1
        for line,kind in scan_text(p.read_bytes()):
            problems.append((rel,line,kind))
    if "--history" in sys.argv:
        revisions=[v for v in git_bytes("rev-list","--all").decode().splitlines() if v]
        seen=set()
        for rev in revisions:
            tree=git_bytes("ls-tree","-rz",rev).split(b"\x00")
            for entry in tree:
                if not entry or b"\t" not in entry:continue
                header,rawpath=entry.split(b"\t",1)
                pieces=header.split()
                if len(pieces)!=3 or pieces[1]!=b"blob":continue
                oid=pieces[2].decode()
                rel=rawpath.decode(errors="replace")
                if is_protected(rel):
                    problems.append((rel,0,"protected_file_in_history"))
                    continue
                if oid in seen:continue
                seen.add(oid)
                size=int(git_bytes("cat-file","-s",oid).decode().strip())
                if size>5_000_000:continue
                checked+=1
                for line,kind in scan_text(git_bytes("cat-file","blob",oid)):
                    problems.append((rel,line,"history_"+kind))
    for name,line,kind in problems:
        # Do NOT output matched text, values, git contents or credential fragments.
        print(f"SECRET_SCAN_FAIL file={name} line={line} reason={kind}",file=sys.stderr)
    if problems:raise SystemExit(2)
    print("SECRET_SCAN_PASS inspected="+str(checked)+" history="+str("--history" in sys.argv))

if __name__=="__main__":inspect()

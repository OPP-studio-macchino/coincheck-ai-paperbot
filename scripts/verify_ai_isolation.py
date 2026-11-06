"""Disposable CI-only two-container handoff acceptance. Never uses real keys/DB.

Run after building coincheck-ai-lab-ci and coincheck-ai-generator-ci. Containers
are uniquely named; the fresh fixture volume is left to the disposable runner.
No existing project, volume, Bot, API or exchange is addressed by this script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_lab.contracts import canonical_json
from ai_lab.dataset import make_fixture
from ai_lab.engine import prepare_proposal
from ai_lab.phase2_artifacts import validate_artifact, write_json_file
from ai_lab.phase2_policy import RESERVATION_MICROUSD


def docker(*args, timeout=420):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # No raw Docker logs, environment values, or exception strings in CI.
        raise RuntimeError("AI_ISOLATION_DOCKER_FAILED_" + args[0].upper())
    return result.stdout


def inspect(name, uid):
    item = json.loads(docker("inspect", name))[0]
    host, config = item["HostConfig"], item["Config"]
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert "ALL" in host["CapDrop"] and not host.get("CapAdd")
    assert any("no-new-privileges" in value for value in host["SecurityOpt"])
    assert host["PidsLimit"] == 32 and host["Memory"] > 0 and host["NanoCpus"] > 0
    assert not host["Privileged"] and not host.get("Devices") and not host.get("PortBindings")
    assert config["User"] == f"{uid}:{uid}"
    assert not any("OPENAI_API_KEY" in value or "COINCHECK_" in value for value in config["Env"])
    assert not any("docker.sock" in value.get("Source", "") for value in item["Mounts"])
    assert next(value for value in item["Mounts"] if value["Destination"] == "/input")["RW"] is False
    return {"uid": uid, "network": "none", "read_only": True, "cap_drop": "ALL",
            "pids": host["PidsLimit"], "memory": host["Memory"], "nano_cpus": host["NanoCpus"]}


GENERATOR_CODE = r'''
from pathlib import Path
import importlib.util,json,os
from ai_lab.phase2 import main
from ai_lab.phase2_artifacts import read_envelope,load_candidate
from ai_lab.phase2_budget import Phase2Budget
assert os.getuid()==10002
assert not Path('/opt/generator/app').exists()
assert importlib.util.find_spec('psycopg') is None
assert importlib.util.find_spec('ai_lab.engine') is None
assert importlib.util.find_spec('ai_lab.evaluator') is None
assert not Path('/var/run/docker.sock').exists()
try:
    Path('/opt/generator/forbidden-write').write_text('x')
    raise AssertionError('ROOTFS_WRITABLE')
except OSError:
    pass
assert main(['init','--state','/var/lib/ai-generator','--limits','/input/limits.json'])==0
args=['generate','--state','/var/lib/ai-generator','--context','/input/request.json']
assert main(args)==0
assert main(args)==0
with Phase2Budget(Path('/var/lib/ai-generator/fixture')) as budget:
    assert budget.status()['total_attempts']==1
request=read_envelope(Path('/input/request.json'))
artifact=load_candidate(Path('/var/lib/ai-generator/fixture/artifacts')/(request['binding']['request_hash']+'.json'),expected_envelope=request)
assert artifact['generation']['mode']=='fixture'
print('ARTIFACT_JSON='+json.dumps(artifact,separators=(',',':')))
'''

EVALUATOR_CODE = r'''
from pathlib import Path
import json,os
from ai_lab.__main__ import main
from ai_lab.ledger import Ledger
assert os.getuid()==10001
assert not os.environ.get('OPENAI_API_KEY_FILE') and not os.environ.get('OPENAI_API_KEY')
args=['evaluate-artifact','--csv','/input/synthetic.csv','--artifact','/input/candidate.json',
      '--workspace','/var/lib/ai-lab/research','--run-id','container-handoff','--buy-budget-jpy','13000']
assert main(args)==0
ledger=Ledger('/var/lib/ai-lab/research')
try:
    snapshot=ledger.snapshot('container-handoff')
    assert len(snapshot['trials'])==1
    assert snapshot['run']['manifest']['actual_openai_calls']==0
    assert snapshot['run']['manifest']['generation_mode']=='fixture'
    print('EVALUATION_JSON='+json.dumps({'status':snapshot['run']['status'],
          'outcome':snapshot['run']['result']['outcome'],'trials':len(snapshot['trials']),
          'generation_mode':'fixture','actual_openai_calls':0},separators=(',',':')))
finally:
    ledger.close()
'''


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("DISPOSABLE_CI_RUNNER_REQUIRED")
    suffix = uuid.uuid4().hex[:12]
    generator_name, evaluator_name = "ccai-generator-" + suffix, "ccai-evaluator-" + suffix
    volume = "ccai-fixture-" + suffix
    names = []
    with tempfile.TemporaryDirectory(prefix="ccai-input-") as temporary:
        directory = Path(temporary).resolve()
        directory.chmod(0o700)
        data = make_fixture(directory / "synthetic.csv", days=180, seed=47)
        request = prepare_proposal(data, buy_budget_jpy=13000)
        write_json_file(directory / "request.json", request)
        write_json_file(directory / "limits.json", {
            "per_call_limit_microusd":RESERVATION_MICROUSD,
            "daily_limit_microusd":RESERVATION_MICROUSD,
            "total_limit_microusd":RESERVATION_MICROUSD,
            "daily_call_limit":1,"total_call_limit":1})
        for path in directory.iterdir(): path.chmod(0o444)
        directory.chmod(0o555)  # Nonsecret, explicitly read-only cross-UID handoff.
        common = ["--network", "none", "--read-only", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges", "--pids-limit", "32",
                  "--memory", "512m", "--cpus", "1",
                  "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m",
                  "--mount", f"type=bind,source={directory},target=/input,readonly",
                  "--env", "DRY_RUN=true", "--env", "LIVE_TRADING_ENABLED=false",
                  "--env", "NEWS_MODE=mock", "--entrypoint", "python"]
        try:
            docker("volume", "create", "--label", "com.opp.scope=ai-isolation-ci", volume)
            docker("create", "--name", generator_name, *common,
                   "--mount", f"type=volume,source={volume},target=/var/lib/ai-generator",
                   "coincheck-ai-generator-ci", "-c", GENERATOR_CODE)
            names.append(generator_name)
            generator_config = inspect(generator_name,10002)
            output = docker("start", "--attach", generator_name)
            raw = next(line.split("=",1)[1] for line in output.splitlines() if line.startswith("ARTIFACT_JSON="))
            artifact = validate_artifact(json.loads(raw),expected_envelope=request)
            directory.chmod(0o700)
            write_json_file(directory/"candidate.json",artifact)
            (directory/"candidate.json").chmod(0o444)
            directory.chmod(0o555)
            docker("create", "--name", evaluator_name, *common,
                   "--tmpfs", "/var/lib/ai-lab:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001,mode=0700",
                   "coincheck-ai-lab-ci", "-c", EVALUATOR_CODE)
            names.append(evaluator_name)
            evaluator_config = inspect(evaluator_name,10001)
            output = docker("start", "--attach", evaluator_name)
            result = json.loads(next(line.split("=",1)[1] for line in output.splitlines() if line.startswith("EVALUATION_JSON=")))
            print(canonical_json({"status":"PASS","generator":generator_config,
                  "evaluator":evaluator_config,"result":result,
                  "artifact_hash":artifact["artifact_hash"],"live_api_calls":0}))
        finally:
            directory.chmod(0o700)
            for name in names:
                # Exact containers created by this invocation only; never -v.
                docker("rm", name, timeout=30)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("AI_ISOLATION_FAILED",file=sys.stderr)
        raise SystemExit(1) from None

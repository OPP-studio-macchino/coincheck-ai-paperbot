"""Offline CLI. There is no command for trading, deployment or live AI calls."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .contracts import LabError, ResearchDeadline, canonical_json, validate_environment
from .dataset import load_csv, make_fixture, chronological_split
from .ledger import Ledger
from .report import write_report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Invalid arguments may themselves be secret-shaped. Do not echo them.
        self.exit(2, "AI_LAB_ERROR CLI_ARGUMENTS_INVALID\n")


def parser():
    root=Parser(description="Coincheck Bot offline research MVP. No API keys or network required.")
    sub=root.add_subparsers(dest="command",required=True,parser_class=Parser)
    for name in ("demo","run","status"):
        command=sub.add_parser(name)
        command.add_argument("--workspace",type=Path,default=Path("runtime/ai_lab"))
        command.add_argument("--run-id",required=True)
        if name in ("demo","run"):
            command.add_argument("--max-trials",type=int,default=6)
            command.add_argument("--buy-budget-jpy",type=int,default=50000)
        if name == "demo":
            command.add_argument("--days",type=int,default=180)
            command.add_argument("--seed",type=int,default=47)
        if name == "run":
            command.add_argument("--csv",type=Path,required=True)
            command.add_argument("--as-of")
    validate=sub.add_parser("validate-data")
    validate.add_argument("--csv",type=Path,required=True)
    validate.add_argument("--as-of")
    return root


def _cutoff(value):
    if value is None:
        return None
    try:
        result=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        raise LabError("AS_OF_INVALID") from None
    if result.utcoffset() is None or result.utcoffset().total_seconds()!=0:
        raise LabError("AS_OF_UTC_REQUIRED")
    # A future simulated cutoff is not evidence that future bars are confirmed.
    if result > datetime.now(timezone.utc):
        raise LabError("AS_OF_FUTURE")
    return result


def _save(snapshot,ledger):
    run_id=snapshot["run"]["run_id"]
    output=ledger.root/(run_id+".json")
    temporary=ledger.root/(run_id+".json.partial")
    if output.is_symlink() or temporary.is_symlink():
        raise LabError("REPORT_PATH_INVALID")
    with temporary.open("w",encoding="utf-8") as stream:
        stream.write(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary,0o600)
    temporary.replace(output)
    report=ledger.root/(run_id+".html")
    write_report(snapshot,report)
    result=snapshot["run"].get("result") or {}
    print(canonical_json({"run_id":run_id,"status":snapshot["run"]["status"],
                          "outcome":result.get("outcome","INCOMPLETE"),
                          "trial_count":len(snapshot["trials"]),"reasons":result.get("reasons",[]),
                          "report":str(report),"actual_openai_calls":0,"deployment_enabled":False}))


def main(argv=None):
    args=parser().parse_args(argv)
    validate_environment(os.environ)
    if args.command == "validate-data":
        data=load_csv(args.csv,as_of=_cutoff(args.as_of))
        print(canonical_json({"data":data.metadata(),"splits":chronological_split(data)}))
        return 0
    ledger=Ledger(args.workspace)
    try:
        if args.command == "status":
            _save(ledger.snapshot(args.run_id),ledger)
            return 0
        if args.command == "demo":
            input_dir=ledger.root/"inputs"
            if input_dir.is_symlink():
                raise LabError("INPUT_PATH_INVALID")
            input_dir.mkdir(exist_ok=True,mode=0o700)
            file=input_dir/f"synthetic-{args.days}d-seed{args.seed}.csv"
            if file.is_symlink():
                raise LabError("INPUT_PATH_INVALID")
            data=load_csv(file) if file.exists() else make_fixture(file,days=args.days,seed=args.seed)
            if data.source!="synthetic" or len(data.candles)!=args.days*288:
                raise LabError("FIXTURE_IDENTITY_INVALID")
        else:
            data=load_csv(args.csv,as_of=_cutoff(args.as_of))
        from .engine import run_research
        def progress(index,status):
            print(canonical_json({"event":"trial_finished","trial":index+1,"status":status}),flush=True)
        snapshot=run_research(data,ledger,args.run_id,max_trials=args.max_trials,
                              buy_budget_jpy=args.buy_budget_jpy,progress=progress,
                              fixture_seed=args.seed if args.command=="demo" else None)
        _save(snapshot,ledger)
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LabError as error:
        # All LabError sites use fixed codes, never input values.
        print("AI_LAB_ERROR "+str(error).upper())
        raise SystemExit(1) from None
    except ResearchDeadline:
        print("AI_LAB_ERROR TIME_LIMIT")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("AI_LAB_ERROR INTERRUPTED")
        raise SystemExit(130) from None
    except Exception as error:
        print("AI_LAB_ERROR "+type(error).__name__.upper())
        raise SystemExit(1) from None

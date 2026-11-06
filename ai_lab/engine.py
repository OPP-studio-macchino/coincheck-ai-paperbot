"""Finite offline research. The generator never receives final-test results."""
from __future__ import annotations

import contextlib
import hashlib
import math
import platform
import re
import signal
import statistics
import threading
import time
from datetime import timedelta
from pathlib import Path

from .contracts import BASELINE, LabError, ResearchDeadline, POLICY, POLICY_HASH, digest, validate_candidate
from .dataset import Dataset, chronological_split, validate_dataset
from .evaluator import evaluate
from .ledger import Ledger
from .provider import FixtureProvider
from .artifact_provider import ArtifactProvider
from .proposal_context import make_envelope
from .screening import screen


def source_identity() -> dict:
    root = Path(__file__).resolve().parent.parent
    paths = sorted((root / "ai_lab").glob("*.py"))
    paths += [root/"ai_lab/policy.json", root/"app/strategy.py",
              root/"docs/ai-self-improvement/SOURCE_OF_TRUTH.md"]
    hashes = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {"source_hash":digest(hashes), "files":hashes,
            "python_version":platform.python_version()}


def training_summary(dataset: Dataset, start: int, end: int) -> dict:
    closes=[x.close for x in dataset.candles[start:end]]
    returns=[math.log(closes[i]/closes[i-1]) for i in range(1,len(closes))]
    change=(closes[-1]/closes[0]-1)*100
    volatility=statistics.pstdev(returns)*math.sqrt(288)*100 if returns else 0.0
    regime="high_volatility" if volatility>3 else "uptrend" if change>2 else "downtrend" if change < -2 else "range"
    return {"start_utc":dataset.candles[start].start.isoformat(),
            "end_utc":(dataset.candles[end-1].start+timedelta(minutes=5)).isoformat(),
            "rows":end-start, "return_pct":round(change,6),
            "daily_volatility_pct":round(volatility,6), "regime":regime}


def _initial_context(dataset: Dataset, splits: dict) -> dict:
    return {"training_summary": training_summary(dataset, *splits["train"]),
            "development_feedback": [], "parent_candidate": BASELINE.to_dict(),
            "trial_index": 0, "policy_version": POLICY["policy_version"]}


def prepare_proposal(dataset: Dataset, *, buy_budget_jpy: int = 50000) -> dict:
    """Export only train statistics, plus local bindings for one offline trial."""
    if type(buy_budget_jpy) is not int or not 0 < buy_budget_jpy <= POLICY["buy_budget_ceiling_jpy"]:
        raise LabError("BUY_BUDGET_INVALID")
    validate_dataset(dataset)
    splits = chronological_split(dataset, POLICY["warmup_days"])
    return make_envelope(_initial_context(dataset, splits), dataset_hash=dataset.sha256,
                         dataset_metadata=dataset.metadata(), splits=splits,
                         buy_budget_jpy=buy_budget_jpy,
                         source_hash=source_identity()["source_hash"])


def _subset(dataset: Dataset, end: int) -> Dataset:
    # Pass only observations available by the evaluation end, even to the evaluator.
    return Dataset(dataset.candles[:end],digest({"parent":dataset.sha256,"end":end}),
                   dataset.source,dataset.available_at[:end] if dataset.available_at else (),False)


def _evaluate(dataset, candidate, start, end, budget, multiplier=1.0):
    result = evaluate(_subset(dataset,end),candidate,start_index=start,end_index=end,
                      buy_budget_jpy=budget,cost_multiplier=multiplier).to_dict()
    metrics = result.pop("metrics")
    return {**metrics,"trades":result["trades"],"assumptions":result["assumptions"],
            "start_utc":result["start_utc"],"end_utc":result["end_utc"]}


@contextlib.contextmanager
def _deadline():
    if threading.current_thread() is not threading.main_thread():
        raise LabError("ENGINE_MAIN_THREAD_REQUIRED")
    def expired(*_):
        raise ResearchDeadline("TIME_LIMIT")
    expires_at=time.monotonic()+POLICY["max_runtime_seconds"]
    def check():
        if time.monotonic()>=expires_at:
            raise ResearchDeadline("TIME_LIMIT")
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,POLICY["max_runtime_seconds"])
    try:
        yield check
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)


def _code(error: Exception) -> str:
    value=str(error).upper()
    return value if re.fullmatch(r"[A-Z0-9_]{1,80}",value) else "CANDIDATE_FAILED"


def run_research(dataset: Dataset, ledger: Ledger, run_id: str, *,
                 max_trials: int = 6, buy_budget_jpy: int = 50000,
                 provider=None, progress=None, fixture_seed: int | None = None) -> dict:
    if type(max_trials) is not int or not 1 <= max_trials <= POLICY["max_trials"]:
        raise LabError("TRIAL_LIMIT")
    if type(buy_budget_jpy) is not int or not 0 < buy_budget_jpy <= POLICY["buy_budget_ceiling_jpy"]:
        raise LabError("BUY_BUDGET_INVALID")
    validate_dataset(dataset)
    splits=chronological_split(dataset,POLICY["warmup_days"])
    provider=FixtureProvider() if provider is None else provider
    if type(provider) not in (FixtureProvider, ArtifactProvider):
        raise LabError("LIVE_PROVIDER_DISABLED")
    identity = source_identity()
    artifact_mode = type(provider) is ArtifactProvider
    if artifact_mode:
        if max_trials != 1 or fixture_seed is not None:
            raise LabError("PHASE2_SINGLE_CANDIDATE_REQUIRED")
        # Never trust the producer's claim about data, settings or training input.
        expected = make_envelope(_initial_context(dataset, splits), dataset_hash=dataset.sha256,
                                 dataset_metadata=dataset.metadata(), splits=splits,
                                 buy_budget_jpy=buy_budget_jpy,
                                 source_hash=identity["source_hash"])
        provider.verify_envelope(expected)
    provider_metadata = provider.metadata()
    generation_mode = provider_metadata.get("generation_mode", "fixture")
    manifest={"spec_version":POLICY["canonical_spec_version"],
              "policy_version":POLICY["policy_version"],"policy_hash":POLICY_HASH,
              "dataset_hash":dataset.sha256,"provenance":dataset.metadata(),
              "splits":{key:list(value) for key,value in splits.items()},
              "source":identity,"baseline_hash":BASELINE.strategy_hash,
              "max_trials":max_trials,"buy_budget_jpy":buy_budget_jpy,
              "initial_cash_jpy":POLICY["initial_cash_jpy"],"execution":POLICY["execution"],
              "screening":POLICY["screening"],"provider":type(provider).__name__,
              "provider_contract":provider_metadata,
              "provider_mode":"offline_artifact" if artifact_mode else "offline_fixture",
              "generation_mode":generation_mode,"model_id":provider_metadata["model_id"],
              "fixture_seed":fixture_seed,
              "actual_openai_calls":0,"actual_openai_calls_scope":"offline_evaluator_only",
              "upstream_openai_calls_reported":1 if generation_mode=="openai_responses" else 0,
              "producer_authenticated":False,"deployment_enabled":False}
    with ledger.single_writer():
        previous=ledger.begin_run(run_id,manifest)
        if artifact_mode:
            provider.verify_saved_trials(ledger.trials(run_id), complete=previous["status"] == "COMPLETED")
        if previous["status"] == "COMPLETED":
            return ledger.snapshot(run_id)
        with _deadline() as check:
            return _run(dataset,ledger,run_id,manifest,splits,provider,progress,check)


def _run(dataset,ledger,run_id,manifest,splits,provider,progress,check):
    import json
    budget=manifest["buy_budget_jpy"]
    dev_start,dev_end=splits["development"]
    multiplier=POLICY["screening"]["stress_cost_multiplier"]
    base=_evaluate(dataset,BASELINE,dev_start,dev_end,budget)
    base_stress=_evaluate(dataset,BASELINE,dev_start,dev_end,budget,multiplier)
    train=training_summary(dataset,*splits["train"])
    # Align the internal development boundaries to 4h to compare all timeframes.
    blocks=(dev_end-dev_start)//48
    folds=[(dev_start+(blocks*i//3)*48,dev_start+(blocks*(i+1)//3)*48) for i in range(3)]
    if any(end<=start for start,end in folds):
        raise LabError("DEVELOPMENT_TOO_SHORT")
    seen={BASELINE.strategy_hash}
    feedback=[]
    parent=BASELINE
    parent_profit=base["net_profit_jpy"]
    saved_trials = ledger.trials(run_id)
    if type(provider) is ArtifactProvider:
        provider.verify_saved_trials(saved_trials)
    old={x["trial_index"]:x for x in saved_trials}
    for index in range(manifest["max_trials"]):
        check()
        row=old.get(index)
        if row and row["status"] not in ("RESERVED","GENERATED"):
            if row["candidate_hash"]:
                seen.add(row["candidate_hash"])
            if row["result"] and row["candidate"]:
                result=json.loads(row["result"])
                if "development" in result:
                    prior=validate_candidate(json.loads(row["candidate"]))
                    feedback.append(_feedback(prior,result))
                    if result["development"]["net_profit_jpy"]>parent_profit:
                        parent,parent_profit=prior,result["development"]["net_profit_jpy"]
            continue
        if row and row["status"] == "RESERVED":
            # A lost generator result is never silently regenerated at the same index.
            ledger.finish_trial(run_id,index,"FAILED",error_code="INTERRUPTED_GENERATION")
            continue
        if row:
            candidate=validate_candidate(json.loads(row["candidate"]))
        else:
            ledger.reserve_trial(run_id,index,manifest["max_trials"])
            context={"training_summary":train,"development_feedback":feedback,
                     "parent_candidate":parent.to_dict(),"trial_index":index,
                     "policy_version":POLICY["policy_version"]}
            ledger.record_input(run_id,index,context)
            try:
                candidate=validate_candidate(provider.propose(context,index))
                check()
            except LabError as error:
                if _code(error)=="TIME_LIMIT":raise
                ledger.finish_trial(run_id,index,"FAILED",error_code=_code(error))
                if progress:progress(index,"FAILED")
                continue
            except Exception:
                ledger.finish_trial(run_id,index,"FAILED",error_code="GENERATOR_FAILED")
                if progress:progress(index,"FAILED")
                continue
            ledger.candidate(run_id,index,candidate.to_dict(),candidate.strategy_hash)
        if candidate.strategy_hash in seen:
            ledger.finish_trial(run_id,index,"DUPLICATE",error_code="SEMANTIC_DUPLICATE")
            if progress:progress(index,"DUPLICATE")
            continue
        seen.add(candidate.strategy_hash)
        try:
            dev=_evaluate(dataset,candidate,dev_start,dev_end,budget)
            stress=_evaluate(dataset,candidate,dev_start,dev_end,budget,multiplier)
            fold_results=[_evaluate(dataset,candidate,start,end,budget) for start,end in folds]
            reasons=screen(dev,base,stress,base_stress,fold_results)
            result={"development":dev,"stress":stress,"folds":fold_results,
                    "gate_reasons":reasons,"parent_strategy_hash":parent.strategy_hash}
            state="REJECTED" if reasons else "SHORTLISTED"
            ledger.finish_trial(run_id,index,state,result)
            feedback.append(_feedback(candidate,result))
            if dev["net_profit_jpy"]>parent_profit:
                parent,parent_profit=candidate,dev["net_profit_jpy"]
            if progress:progress(index,state)
        except LabError as error:
            if _code(error)=="TIME_LIMIT":raise
            ledger.finish_trial(run_id,index,"FAILED",error_code=_code(error))
            if progress:progress(index,"FAILED")
    shortlist=[]
    check()
    for row in ledger.trials(run_id):
        if row["status"] == "SHORTLISTED":
            result=json.loads(row["result"])
            shortlist.append((result["development"]["net_profit_jpy"],row["candidate_hash"],row))
    final_result={"outcome":"NO_IMPROVEMENT","reasons":[],"baseline_development":base,
                  "baseline_stress":base_stress,"final_test":None,
                  "selected_candidate":None,"actual_openai_calls":0,
                  "actual_openai_calls_scope":"offline_evaluator_only",
                  "upstream_openai_calls_reported":manifest["upstream_openai_calls_reported"],
                  "generation_mode":manifest["generation_mode"],"deployment_enabled":False}
    if not shortlist:
        codes=set()
        for row in ledger.trials(run_id):
            if row["result"]:
                codes.update(json.loads(row["result"]).get("gate_reasons",[]))
        final_result["reasons"]=sorted(codes) or ["NO_VALID_UNIQUE_CANDIDATE"]
    else:
        _,_,winner=sorted(shortlist,key=lambda item:(-item[0],item[1]))[0]
        candidate=validate_candidate(json.loads(winner["candidate"]))
        final_result["selected_candidate"]=candidate.to_dict()
        start,end=splits["holdout"]
        start_epoch=int(dataset.candles[start].start.timestamp())
        end_epoch=int((dataset.candles[end-1].start+timedelta(minutes=5)).timestamp())
        try:
            check()
            saved=ledger.reserve_final(run_id,dataset.sha256,candidate.strategy_hash,start_epoch,end_epoch)
        except LabError as error:
            if _code(error)!="HOLDOUT_ALREADY_CONSUMED":raise
            final_result.update(outcome="HOLDOUT_ALREADY_CONSUMED",reasons=["HOLDOUT_ALREADY_CONSUMED"])
        else:
            if saved is None:
                candidate_final=_evaluate(dataset,candidate,start,end,budget)
                candidate_stress=_evaluate(dataset,candidate,start,end,budget,multiplier)
                baseline_final=_evaluate(dataset,BASELINE,start,end,budget)
                baseline_stress=_evaluate(dataset,BASELINE,start,end,budget,multiplier)
                reasons=screen(candidate_final,baseline_final,candidate_stress,baseline_stress)
                saved={"candidate":candidate_final,"baseline":baseline_final,
                       "stress":candidate_stress,"baseline_stress":baseline_stress,
                       "gate_reasons":reasons,"passed":not reasons}
                ledger.finish_final(run_id,dataset.sha256,saved)
            final_result["final_test"]=saved
            final_result["reasons"]=saved["gate_reasons"]
            final_result["outcome"]=("DEMO_ONLY" if dataset.source=="synthetic" else "REVIEW_REQUIRED") if saved["passed"] else "HOLDOUT_FAILED"
    if type(provider) is ArtifactProvider:
        provider.verify_saved_trials(ledger.trials(run_id), complete=True)
    ledger.finish_run(run_id,final_result)
    return ledger.snapshot(run_id)


def _feedback(candidate,result):
    dev=result["development"]
    return {"candidate_hash":candidate.strategy_hash,"net_profit_jpy":dev["net_profit_jpy"],
            "max_drawdown_pct":dev["max_drawdown_pct"],"closed_trades":dev["closed_trades"],
            "gate_reasons":result["gate_reasons"]}

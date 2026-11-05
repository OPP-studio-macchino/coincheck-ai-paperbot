import copy
import unittest
from unittest.mock import patch

from ai_lab.contracts import BASELINE, LabError, POLICY, canonical_json, validate_candidate
from ai_lab.provider import (FixtureProvider, FixtureTransport, ResponsesContractProvider,
                             PROMPT, build_responses_request, parse_responses_output)


def context(index=0):
    return {"training_summary":{"start_utc":"2020-01-01T00:00:00Z","end_utc":"2020-04-01T00:00:00Z",
                                "rows":1000,"return_pct":3.0,"daily_volatility_pct":2.0,"regime":"uptrend"},
            "development_feedback":[],"parent_candidate":BASELINE.to_dict(),
            "trial_index":index,"policy_version":POLICY["policy_version"]}


def response(candidate=None):
    return {"id":"fixture-response","status":"completed","error":None,"incomplete_details":None,
            "output":[{"type":"message","role":"assistant","status":"completed",
                       "content":[{"type":"output_text","text":canonical_json(candidate or BASELINE.to_dict())}]}]}


class ProviderTests(unittest.TestCase):
    def test_responses_wire_contract_with_no_network(self):
        transport=FixtureTransport(response())
        adapter=ResponsesContractProvider(transport)
        with patch("socket.socket",side_effect=AssertionError("NETWORK_FORBIDDEN")):
            candidate=adapter.propose(context(),0)
        self.assertEqual(validate_candidate(candidate).strategy_hash,BASELINE.strategy_hash)
        request=transport.last_request
        self.assertEqual(transport.calls,1)
        self.assertEqual(request["tools"],[])
        self.assertEqual(request["tool_choice"],"none")
        self.assertIs(request["store"],False)
        self.assertIs(request["text"]["format"]["strict"],True)
        self.assertIs(request["text"]["format"]["schema"]["additionalProperties"],False)
        self.assertEqual(request["input"][0],{"role":"developer","content":PROMPT})

    def test_refusal_and_incomplete_are_not_candidates(self):
        for state in ("incomplete","failed","cancelled","in_progress"):
            item=response();item["status"]=state
            with self.assertRaises(LabError): parse_responses_output(item)
        item=response()
        item["output"][0]["content"]=[{"type":"refusal","refusal":"UNTRUSTED_DO_NOT_LOG"}]
        with self.assertRaisesRegex(LabError,"^MODEL_REFUSAL$"):
            parse_responses_output(item)

    def test_ambiguous_or_tool_output_rejected(self):
        cases=[]
        item=response();item["output"].append(copy.deepcopy(item["output"][0]));cases.append(item)
        item=response();item["output"][0]["content"]*=2;cases.append(item)
        item=response();item["output"].append({"type":"function_call","name":"shell"});cases.append(item)
        item=response();item["output"]=[];cases.append(item)
        for item in cases:
            with self.assertRaises(LabError): parse_responses_output(item)

    def test_json_boundaries_and_forbidden_output_keys(self):
        bad=BASELINE.to_dict();bad["buy_budget_jpy"]=100000
        with self.assertRaises(LabError): parse_responses_output(response(bad))
        for content in ('{"name":"a","name":"b"}', '{"x":NaN}', '{"x":'+'9'*5000+'}', ' '*20000):
            item=response();item["output"][0]["content"][0]["text"]=content
            with self.assertRaises(LabError): parse_responses_output(item)

    def test_holdout_and_unstructured_text_cannot_enter_request(self):
        for key in ("holdout","test_profit","news_text","api_key"):
            item=context();item[key]="UNTRUSTED_DO_NOT_LOG"
            with self.assertRaises(LabError): build_responses_request(item,model="fixture-model")
        item=context();item["training_summary"]["instruction"]="UNTRUSTED_DO_NOT_LOG"
        with self.assertRaises(LabError): build_responses_request(item,model="fixture-model")
        item=context();item["development_feedback"]=[{"holdout_profit":100}]
        with self.assertRaises(LabError): build_responses_request(item,model="fixture-model")

    def test_all_eight_variations_and_feedback_are_valid(self):
        provider=FixtureProvider()
        seen=set()
        with patch("socket.socket",side_effect=AssertionError("NETWORK_FORBIDDEN")):
            for index in range(8):
                candidate=validate_candidate(provider.propose(context(index),index))
                seen.add(candidate.strategy_hash)
        self.assertEqual(len(seen),8)
        first=context(2)
        second=context(2)
        second["parent_candidate"]["ema_fast"]=34
        self.assertNotEqual(provider.propose(first,2)["ema_fast"],provider.propose(second,2)["ema_fast"])
        first=context(4);second=context(4);second["training_summary"]["regime"]="high_volatility"
        self.assertNotEqual(provider.propose(first,4)["volatility_cap_pct"],provider.propose(second,4)["volatility_cap_pct"])

    def test_no_live_transport_or_model_endpoint_substitution(self):
        for wrong in (lambda _:response(), object(), {}, False):
            with self.assertRaises(LabError): ResponsesContractProvider(wrong)
        with self.assertRaises(LabError): build_responses_request(context(),model="https://example.invalid")
        with self.assertRaises(LabError): build_responses_request(context(),model="fixture-model",max_output_tokens=1000000)

    def test_reasoning_item_is_data_not_an_executable_tool(self):
        item=response()
        item["output"].insert(0,{"type":"reasoning","summary":[]})
        self.assertEqual(parse_responses_output(item)["ema_fast"],21)


if __name__ == "__main__":unittest.main()

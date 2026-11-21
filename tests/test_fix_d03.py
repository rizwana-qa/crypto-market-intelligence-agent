"""D-03: the reviewer path has gaps. The model is a script of canned replies, so nothing here calls an AI.

    python tests/test_fix_d03.py -v

L5: a revision that adds an invented number was never checked again.
L6: the code number check was only text for the AI reviewer, so an approving AI could pass a flagged draft.
L7: an unusable reviewer reply fails open (by design) but left no marker.
L8: when the revision also fails, the first draft ships and nothing recorded that a problem stayed open.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import scenarios  # noqa: E402

CLEAN = dict(alert=True, coins=["BTC"], sources=["Volatility rule"],
             message="BTC volatility is at its highest rank in the market data.", reasoning="BTC volatility rank is at or above 95.")
INVENTED = dict(CLEAN, message="BTC fell 61.3% in one day, a record.", reasoning="Fell 61.3%.")
INVENTED_TWO = dict(CLEAN, message="BTC volatility is 9.87% and its rank is 99.9.", reasoning="Volatility 9.87%, rank 99.9.")
REJECT = {"approved": False, "problems": ["rank not in the evidence"]}
APPROVE = {"approved": True, "problems": []}


def tool_calls():
    calls = [("get_market_summary", {"coin": c}) for c in ("BTC", "ETH", "XRP")] + [("get_recent_decisions", {}), ("search_playbook", {"query": "volatility alert rules"})]
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}


def say(body):
    return {"role": "assistant", "content": body if isinstance(body, str) else json.dumps(body)}


class ReviewerCase(unittest.TestCase):
    def setUp(self):
        self.logs = []
        patcher = mock.patch.object(agent, "log", self.logs.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tools = scenarios.fake_tools(scenarios.btc_spike(scenarios.calm()), [])

    def decide(self, replies, reviewer=True):
        with mock.patch.object(agent, "call_llm", side_effect=replies) as fake:
            decision, evidence = agent.decide("agent", reviewer, self.tools)
        self.assertEqual(fake.call_count, len(replies), "the code made fewer model calls than the script holds")
        return decision, evidence

    def review(self, replies, draft):
        """review_and_revise alone: the draft and the evidence come from a loop run, then the reviewer gets its scripted replies."""
        with mock.patch.object(agent, "call_llm", side_effect=[tool_calls(), say(draft)]):
            first, evidence = agent.run_agent_loop(self.tools)
        with mock.patch.object(agent, "call_llm", side_effect=replies) as fake:
            result = agent.review_and_revise(first, evidence)
        return result, fake.call_count

    def status_lines(self):
        return [line for line in self.logs if "review_status=" in line]


class NumberCheckIsBinding(ReviewerCase):
    def test_the_fixtures_are_what_the_tests_assume(self):
        _, evidence = self.decide([tool_calls(), say(CLEAN)], reviewer=False)
        shown = agent.evidence_text(evidence)
        self.assertEqual(agent.unsupported_numbers(CLEAN["message"] + " " + CLEAN["reasoning"], shown), [])
        self.assertIn("61.3", agent.unsupported_numbers(INVENTED["message"], shown))
        self.assertIn("9.87", agent.unsupported_numbers(INVENTED_TWO["message"], shown))

    def test_l6_an_approving_reviewer_cannot_pass_a_draft_the_code_flagged(self):
        decision, _ = self.decide([tool_calls(), say(INVENTED_TWO), say(APPROVE), say(CLEAN)])
        self.assertNotIn("9.87", decision["message"])   # the draft was sent back for a revision, not shipped
        self.assertEqual(decision["review_status"], "revised")

    def test_l6_review_draft_reports_the_code_finding_as_a_problem(self):
        _, evidence = self.decide([tool_calls(), say(CLEAN)], reviewer=False)
        with mock.patch.object(agent, "call_llm", return_value=say(APPROVE)):
            verdict = agent.review_draft(INVENTED_TWO, evidence)
        self.assertFalse(verdict["approved"])
        self.assertIn("9.87", verdict["problems"][0])
        self.assertIn("99.9", verdict["problems"][0])
        with mock.patch.object(agent, "call_llm", return_value=say(APPROVE)):
            self.assertTrue(agent.review_draft(CLEAN, evidence)["approved"])

    def test_l6_the_code_check_still_binds_when_the_reviewer_reply_is_unusable(self):
        decision, _ = self.decide([tool_calls(), say(INVENTED_TWO), say("I think it is fine!"), say(CLEAN)])
        self.assertNotIn("9.87", decision["message"])
        self.assertEqual(decision["review_status"], "revised")

    def test_a_revision_that_still_holds_the_numbers_is_recorded_as_unresolved(self):
        decision, _ = self.decide([tool_calls(), say(INVENTED_TWO), say(APPROVE), say(INVENTED_TWO)])
        self.assertTrue(decision["review_status"].startswith("unresolved: numbers not in the evidence"))
        self.assertIn("9.87", decision["review_status"])


class RevisionIsCheckedAgain(ReviewerCase):
    def test_l5_the_revision_gets_a_second_code_check_and_the_result_is_recorded(self):
        result, calls = self.review([say(REJECT), say(INVENTED)], CLEAN)
        self.assertEqual(calls, 2)                       # one review, one revision, no second AI review (unchanged)
        self.assertEqual(result["message"], INVENTED["message"])   # the loop's behaviour when it runs out of revisions is unchanged
        self.assertEqual(result["review_status"], "unresolved: numbers not in the evidence: 61.3")
        self.assertTrue(any("review_status=unresolved: numbers not in the evidence: 61.3" in line for line in self.status_lines()))

    def test_l5_through_decide_the_status_reaches_the_decision(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN), say(REJECT), say(INVENTED)])
        self.assertEqual(decision["review_status"], "unresolved: numbers not in the evidence: 61.3")

    def test_a_clean_revision_is_recorded_as_revised(self):
        result, calls = self.review([say(REJECT), say(dict(CLEAN, message="BTC volatility is at its highest rank."))], CLEAN)
        self.assertEqual(calls, 2)
        self.assertEqual(result["review_status"], "revised")
        self.assertTrue(any("review_status=revised" in line for line in self.status_lines()))

    def test_with_two_revisions_allowed_a_second_one_is_made_only_while_the_numbers_stay_wrong(self):
        with mock.patch.object(agent, "MAX_REVISIONS", 2):
            result, calls = self.review([say(REJECT), say(INVENTED), say(CLEAN)], CLEAN)
            self.assertEqual((calls, result["review_status"]), (3, "revised"))
            result, calls = self.review([say(REJECT), say(CLEAN)], CLEAN)       # a clean first revision ends the loop
            self.assertEqual((calls, result["review_status"]), (2, "revised"))


class RevisionFails(ReviewerCase):
    def test_l8_a_failed_revision_keeps_the_first_draft_and_records_the_open_problem(self):
        result, calls = self.review([say(REJECT), say("sorry")], CLEAN)
        self.assertEqual(calls, 2)
        self.assertEqual(result["message"], CLEAN["message"])      # unchanged: the first draft is kept
        self.assertEqual(result["review_status"], "unresolved: revision not usable")
        self.assertTrue(any("review_status=unresolved: revision not usable" in line for line in self.status_lines()))

    def test_l8_a_failed_revision_call_is_recorded_the_same_way(self):
        result, _ = self.review([say(REJECT), agent.AgentError("Groq error 500")], CLEAN)
        self.assertEqual(result["review_status"], "unresolved: revision not usable")

    def test_l8_through_decide_the_alert_is_kept_and_marked(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN), say(REJECT), say("sorry")])
        self.assertTrue(decision["alert"])
        self.assertEqual(decision["review_status"], "unresolved: revision not usable")

    def test_no_revision_allowed_is_recorded_too(self):
        with mock.patch.object(agent, "MAX_REVISIONS", 0):
            result, calls = self.review([say(REJECT)], CLEAN)
        self.assertEqual(calls, 1)
        self.assertEqual(result["review_status"], "unresolved: no revision allowed")


class ReviewerReplyUnusable(ReviewerCase):
    def test_l7_a_reply_that_is_not_json_fails_open_and_is_marked(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN), say("I think it is fine!")])
        self.assertTrue(decision["alert"])
        self.assertEqual(decision["message"], CLEAN["message"])
        self.assertEqual(decision["review_status"], "skipped: reviewer reply not usable")
        self.assertTrue(any("review_status=skipped: reviewer reply not usable" in line for line in self.status_lines()))
        self.assertFalse(any("approved the draft" in line for line in self.logs))   # it was not approved, it was not reviewed

    def test_l7_a_wrong_shape_and_a_failed_call_are_marked_as_well(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN), say('{"approved": "maybe"}')])
        self.assertEqual(decision["review_status"], "skipped: reviewer reply not usable")
        decision, _ = self.decide([tool_calls(), say(CLEAN), agent.AgentError("Groq error 500")])
        self.assertTrue(decision["alert"])
        self.assertEqual(decision["review_status"], "skipped: reviewer call failed")


class ApprovedPathIsUnchanged(ReviewerCase):
    def test_an_approved_draft_ships_as_before_with_one_extra_key(self):
        approved, _ = self.decide([tool_calls(), say(CLEAN), say(APPROVE)])
        plain, _ = self.decide([tool_calls(), say(CLEAN)], reviewer=False)
        self.assertEqual(approved["review_status"], "approved")
        self.assertEqual({k: v for k, v in approved.items() if k != "review_status"}, plain)
        self.assertTrue(any("review_status=approved" in line for line in self.status_lines()))

    def test_the_call_count_of_the_approved_path_is_unchanged(self):
        # tool step + final answer + one review: nothing was added to the clean path
        self.decide([tool_calls(), say(CLEAN), say(APPROVE)])

    def test_the_reviewer_off_path_is_untouched(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN)], reviewer=False)
        self.assertEqual(set(decision), {"alert", "coins", "message", "sources", "reasoning", "blocked_reason"})
        self.assertEqual([line for line in self.logs if "REVIEW" in line], [])


class SheetPayloadIsUnchanged(ReviewerCase):
    PAYLOAD_KEYS = {"action", "time", "version", "run_id", "alert", "coins", "message", "sources", "reasoning", "blocked_reason"}

    def posted(self, decision):
        with mock.patch.object(agent, "sheet_post", return_value={"row": 2, "emailed": False}) as post:
            agent.deliver(decision, "Agent loop + reviewer")
        return post.call_args.args[0]

    def test_review_status_never_reaches_the_sheet(self):
        for replies in ([tool_calls(), say(CLEAN), say(APPROVE)], [tool_calls(), say(CLEAN), say(REJECT), say("sorry")]):
            decision, _ = self.decide(replies)
            self.assertIn("review_status", decision)
            payload = self.posted(decision)
            self.assertEqual(set(payload), self.PAYLOAD_KEYS)
            self.assertNotIn("review_status", json.dumps(payload))

    def test_the_payload_is_the_same_with_and_without_the_key(self):
        decision, _ = self.decide([tool_calls(), say(CLEAN), say(APPROVE)])
        bare = {k: v for k, v in decision.items() if k != "review_status"}
        with_key, without_key = self.posted(decision), self.posted(bare)
        for payload in (with_key, without_key):
            payload.pop("time")
        self.assertEqual(with_key, without_key)


if __name__ == "__main__":
    unittest.main()

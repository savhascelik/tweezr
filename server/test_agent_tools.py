"""Unit tests for all 15 agent tools and client context integration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from . import agent_tools
from . import agent


class TestAgentTools(unittest.TestCase):
    def setUp(self):
        agent_tools.new_collection()

    def test_tools_count_and_names(self):
        self.assertEqual(len(agent_tools.TOOLS), 15)
        names = sorted([t.__name__ for t in agent_tools.TOOLS])
        expected = sorted([
            "find_line",
            "get_library_stats",
            "assemble_proposal",
            "get_timeline_state",
            "tweeze_words",
            "remove_segment",
            "reorder_timeline",
            "swap_take",
            "clear_timeline",
            "preview_segment",
            "play_timeline",
            "stop_playback",
            "get_vocabulary",
            "get_line_transcript",
            "request_render",
        ])
        self.assertEqual(names, expected)

    def test_client_context_and_timeline_state(self):
        context = {
            "timeline": [
                {"id": "cand-1", "take_id": "S01_T01", "text": "Hello world", "duration_ms": 1200},
                {"id": "cand-2", "take_id": "S01_T02", "text": "Goodbye", "duration_ms": 800},
            ],
            "candidates": [
                {"id": "cand-1", "take_id": "S01_T01"},
            ],
        }
        agent_tools.set_client_context(context)
        state = agent_tools.get_timeline_state()
        self.assertEqual(state["count"], 2)
        self.assertEqual(state["total_duration_ms"], 2000)
        self.assertEqual(len(state["segments"]), 2)

    def test_tweeze_words_action(self):
        res = agent_tools.tweeze_words("S01_T01:1:0", phrase="never asked", start_ms=1200, end_ms=2100)
        self.assertTrue(res.get("tweezed"))
        self.assertEqual(res["segment"]["text"], "never asked")
        self.assertEqual(res["segment"]["start_ms"], 1200)
        self.assertEqual(res["segment"]["end_ms"], 2100)
        
        coll = agent_tools.collection()
        self.assertEqual(len(coll["actions"]), 1)
        self.assertEqual(coll["actions"][0]["type"], "tweeze_words")

    def test_remove_segment_action(self):
        res = agent_tools.remove_segment(1)
        self.assertEqual(res["removed_index"], 1)
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1], {"type": "remove_segment", "index": 1})

    def test_reorder_timeline_action(self):
        res = agent_tools.reorder_timeline(0, 2)
        self.assertTrue(res["reordered"])
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1], {"type": "reorder_timeline", "from": 0, "to": 2})

    def test_swap_take_action(self):
        res = agent_tools.swap_take(0, "S01_T03:1:0")
        self.assertTrue(res["swapped"])
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1], {"type": "swap_take", "index": 0, "candidate_id": "S01_T03:1:0"})

    def test_clear_timeline_action(self):
        res = agent_tools.clear_timeline()
        self.assertTrue(res["cleared"])
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1], {"type": "clear_timeline"})

    def test_preview_segment_action(self):
        res = agent_tools.preview_segment(candidate_id="S01_T03:1:0")
        self.assertTrue(res["playing"])
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1]["type"], "preview_segment")

    def test_playback_actions(self):
        play_res = agent_tools.play_timeline(start_index=1)
        self.assertTrue(play_res["playing"])

        stop_res = agent_tools.stop_playback()
        self.assertTrue(stop_res["stopped"])

        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-2], {"type": "play_timeline", "start_index": 1})
        self.assertEqual(coll["actions"][-1], {"type": "stop_playback"})

    def test_request_render_action(self):
        res = agent_tools.request_render()
        self.assertTrue(res["requested"])
        coll = agent_tools.collection()
        self.assertEqual(coll["actions"][-1], {"type": "request_render"})

    def test_agent_instruction_mentions_all_tools(self):
        self.assertIn("find_line", agent.INSTRUCTION)
        self.assertIn("tweeze_words", agent.INSTRUCTION)
        self.assertIn("request_render", agent.INSTRUCTION)
        self.assertIn("swap_take", agent.INSTRUCTION)
        self.assertIn("reorder_timeline", agent.INSTRUCTION)


if __name__ == "__main__":
    unittest.main()

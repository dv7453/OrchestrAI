"""Tests for the EpisodicMemory system."""
import pytest
from unittest.mock import patch, MagicMock

from langgraph_browser_agent.memory import EpisodicMemory, Episode, _keyword_similarity


@pytest.fixture
def memory(tmp_path):
    """Create a fresh EpisodicMemory backed by a temp file."""
    db = tmp_path / "test_memory.db"
    return EpisodicMemory(db_path=db)


class TestEpisodeStorage:
    """Test episode storage."""

    def test_store_and_list(self, memory):
        ep = Episode(
            task_text="Go to google.com",
            success=True,
            steps_taken=3,
            duration_seconds=12.5,
        )
        memory.store_episode(ep)

        episodes = memory.list_episodes()
        assert len(episodes) == 1
        assert episodes[0]["task_text"] == "Go to google.com"
        assert episodes[0]["success"] == 1  # SQLite stores bool as int

    def test_store_multiple(self, memory):
        for i in range(5):
            memory.store_episode(
                Episode(task_text=f"Task {i}", success=i % 2 == 0, steps_taken=i)
            )

        episodes = memory.list_episodes()
        assert len(episodes) == 5

    def test_store_with_sub_goals(self, memory):
        ep = Episode(
            task_text="Download VS Code",
            sub_goals=[
                {"description": "Navigate to site", "success_criteria": "Page loaded"},
                {"description": "Click download", "success_criteria": "Download started"},
            ],
            success=True,
            steps_taken=4,
        )
        memory.store_episode(ep)

        episodes = memory.list_episodes()
        assert len(episodes) == 1

    def test_store_with_failure_reason(self, memory):
        ep = Episode(
            task_text="Failed task",
            success=False,
            failure_reason="Timeout after 30 steps",
        )
        memory.store_episode(ep)

        episodes = memory.list_episodes()
        assert len(episodes) == 1


class TestEpisodeRetrieval:
    """Test similarity-based retrieval."""

    def test_retrieve_similar_keyword_fallback(self, memory):
        """Test retrieval using keyword similarity (no sentence-transformers)."""
        memory.store_episode(
            Episode(task_text="Go to google.com and search for cats", success=True, steps_taken=3)
        )
        memory.store_episode(
            Episode(task_text="Download VS Code from website", success=True, steps_taken=5)
        )
        memory.store_episode(
            Episode(task_text="Go to google.com and search for dogs", success=True, steps_taken=2)
        )

        # Patching to force keyword fallback
        with patch("langgraph_browser_agent.memory._embed_text", return_value=None):
            results = memory.retrieve_similar("Go to google.com and search", top_k=2)

        assert len(results) == 2
        # Both google tasks should rank higher than the VS Code task
        assert all("google" in r["task_text"] for r in results)

    def test_retrieve_empty_memory(self, memory):
        results = memory.retrieve_similar("anything", top_k=3)
        assert results == []

    def test_retrieve_respects_top_k(self, memory):
        for i in range(10):
            memory.store_episode(Episode(task_text=f"Task {i}", success=True))

        with patch("langgraph_browser_agent.memory._embed_text", return_value=None):
            results = memory.retrieve_similar("Task", top_k=3)

        assert len(results) == 3

    def test_results_have_similarity_score(self, memory):
        memory.store_episode(Episode(task_text="Navigate to example.com", success=True))

        with patch("langgraph_browser_agent.memory._embed_text", return_value=None):
            results = memory.retrieve_similar("Navigate to example.com")

        assert len(results) >= 1
        assert "similarity_score" in results[0]
        assert results[0]["similarity_score"] > 0

    def test_results_exclude_raw_embedding(self, memory):
        memory.store_episode(Episode(task_text="Test task", success=True))

        with patch("langgraph_browser_agent.memory._embed_text", return_value=None):
            results = memory.retrieve_similar("Test task")

        for r in results:
            assert "task_embedding" not in r


class TestKeywordSimilarity:
    """Test the keyword fallback similarity function."""

    def test_identical_strings(self):
        assert _keyword_similarity("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _keyword_similarity("hello", "goodbye") == 0.0

    def test_partial_overlap(self):
        score = _keyword_similarity("go to google", "go to bing")
        assert 0 < score < 1

    def test_empty_string(self):
        assert _keyword_similarity("", "hello") == 0.0
        assert _keyword_similarity("hello", "") == 0.0


class TestFormatForPrompt:
    """Test prompt formatting of retrieved episodes."""

    def test_format_empty(self, memory):
        result = memory.format_for_prompt([])
        assert result == ""

    def test_format_with_episodes(self, memory):
        episodes = [
            {
                "task_text": "Go to google.com",
                "success": True,
                "sub_goals": [{"description": "Navigate to Google"}],
                "trajectory_summary": "Navigated successfully",
                "similarity_score": 0.95,
                "steps_taken": 3,
            }
        ]
        result = memory.format_for_prompt(episodes)
        assert "Go to google.com" in result
        assert "SUCCESS" in result
        assert "Navigate to Google" in result

    def test_format_with_failed_episode(self, memory):
        episodes = [
            {
                "task_text": "Failed task",
                "success": False,
                "sub_goals": [],
                "failure_reason": "Timeout",
                "similarity_score": 0.8,
                "steps_taken": 10,
            }
        ]
        result = memory.format_for_prompt(episodes)
        assert "FAILED" in result
        assert "Timeout" in result

import time
from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot


def _make_bot() -> WeChatGuiRpaBot:
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace()
    bot._clean_web_query = lambda query: str(query).strip()
    bot._compact_web_text = lambda value, limit=240: str(value)[:limit]
    bot._has_tavily_search_tool = lambda: True
    bot._has_brave_search_tool = lambda: True
    bot._has_volc_web_search_tool = lambda: True
    return bot


def test_aggregate_web_search_returns_all_provider_sections() -> None:
    bot = _make_bot()
    bot._tavily_search = lambda query: f"tavily:{query}"
    bot._brave_search = lambda query: f"brave:{query}"
    bot._volc_web_search = lambda query: f"volc:{query}"

    result = bot._aggregate_web_search("test query")

    assert result == (
        "[tavily]\ntavily:test query\n\n"
        "[brave]\nbrave:test query\n\n"
        "[volc_ark]\nvolc:test query"
    )


def test_aggregate_web_search_keeps_empty_section_on_failure() -> None:
    bot = _make_bot()
    bot._tavily_search = lambda query: "tavily result"
    bot._brave_search = lambda query: (_ for _ in ()).throw(RuntimeError("failed"))
    bot._volc_web_search = lambda query: ""

    result = bot._aggregate_web_search("test")

    assert "[tavily]\ntavily result" in result
    assert "[brave]\n\n" in result
    assert result.endswith("[volc_ark]\n")


def test_aggregate_web_search_runs_providers_in_parallel() -> None:
    bot = _make_bot()

    def slow_result(name: str):
        def search(query: str) -> str:
            time.sleep(0.1)
            return name

        return search

    bot._tavily_search = slow_result("tavily")
    bot._brave_search = slow_result("brave")
    bot._volc_web_search = slow_result("volc")

    started = time.monotonic()
    bot._aggregate_web_search("test")
    elapsed = time.monotonic() - started

    assert elapsed < 0.25

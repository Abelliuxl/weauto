from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class RegionRatio:
    x: float
    y: float
    w: float
    h: float


@dataclass
class PointRatio:
    x: float
    y: float


@dataclass
class UnreadBadgeConfig:
    min_blob_pixels: int = 70


@dataclass
class UnreadBadgeCircleConfig:
    enabled: bool = False
    # Row-local ratios with left-bottom anchor: x from left, y from bottom.
    x: float = 0.86
    y: float = 0.66
    r: float = 0.18


@dataclass
class OcrConfig:
    # Primary OCR backend: rapidocr | paddleocr | cnocr
    backend: str = "rapidocr"
    # Optional A/B backend for side-by-side comparison logs.
    # Empty string means disabled.
    ab_compare_backend: str = ""
    # Sampling rate in [0, 1]. 0 disables A/B runs.
    ab_compare_sample_rate: float = 0.0
    # Truncate A/B preview text in logs.
    ab_compare_max_text_len: int = 120
    # Image enhancement options (used by rapidocr/cnocr; paddle relies on its own pipeline).
    enhance: bool = False
    target_short_side: int = 900
    max_upscale: float = 3.2
    # Paddle/CnOCR language code.
    paddle_lang: str = "ch"
    # Old PaddleOCR argument; harmless when backend is not paddleocr.
    paddle_use_angle_cls: bool = False


@dataclass
class LlmConfig:
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    base_url_env: str = "OPENAI_BASE_URL"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-4o-mini"
    ollama_native: bool = False
    ollama_think: str = ""
    # Controls think mode when using OpenAI-compatible /chat/completions.
    # valid: default | on | off
    openai_compat_think_mode: str = "default"
    temperature: float = 0.3
    presence_penalty: float = 0.2
    frequency_penalty: float = 0.2
    # <=0 means do not send max_tokens to provider (no client-side cap).
    max_tokens: int = 0
    timeout_sec: float = 20.0
    interest_hint: str = ""
    # Reply sarcasm style: off | low | medium | high
    sarcasm_level: str = "low"
    system_prompt: str = (
        "你是微信助手。请基于给定会话标题和预览内容生成简短、礼貌、自然的中文回复，"
        "长度控制在1-2句，不要编造事实。"
    )
    decision_enabled: bool = True
    decision_on_group: bool = True
    decision_on_private: bool = False
    decision_fail_open: bool = False
    # <=0 means do not send max_tokens to provider (no client-side cap).
    decision_max_tokens: int = 0
    decision_read_chat_context: bool = True
    decision_system_prompt: str = (
        "你是消息分流器。给定会话标题、预览和触发原因，判断是否值得自动回复。"
        "仅输出JSON，格式为: {\"decision\":\"reply|skip\",\"reason\":\"<=20字\"}。"
    )
    summary_enabled: bool = True
    # <=0 means do not send max_tokens to provider (no client-side cap).
    summary_max_tokens: int = 0
    summary_system_prompt: str = (
        "你是会话摘要器。给定历史摘要和最近对话，输出新的简短中文摘要，"
        "覆盖关键信息、事实、偏好和待办。只输出摘要正文。"
    )
    anti_repeat_enabled: bool = True
    anti_repeat_window: int = 4
    anti_repeat_similarity: float = 0.82
    anti_repeat_retry: int = 1
    reasoning_exclude: bool = True
    reasoning_effort: str = "low"
    debug_log_payload: bool = False
    debug_log_response: bool = False


@dataclass
class VisionConfig:
    enabled: bool = False
    base_url: str = ""
    base_url_env: str = "OPENAI_BASE_URL"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    model: str = ""
    ollama_native: bool = False
    ollama_think: str = ""
    # Controls think mode when using OpenAI-compatible /chat/completions.
    # valid: default | on | off
    openai_compat_think_mode: str = "default"
    timeout_sec: float = 20.0
    # <=0 means do not send max_tokens to provider (no client-side cap).
    max_tokens: int = 0
    # Some OpenAI-compatible vision models do not support response_format=json_object.
    # Turn this off to skip the first failing request and send raw prompt-only JSON constraints.
    response_format_json_object: bool = True
    fail_open: bool = True
    reasoning_exclude: bool = True
    reasoning_effort: str = "low"
    debug_log_payload: bool = False
    debug_log_response: bool = False
    system_prompt: str = (
        "你是微信聊天截图解析器。请严格输出 JSON，schema=wechat_context_v2；"
        "不要输出 markdown，不要输出额外解释。"
    )


@dataclass
class EmbeddingConfig:
    enabled: bool = False
    base_url: str = "https://api.siliconflow.cn/v1"
    base_url_env: str = "SILICONFLOW_BASE_URL"
    api_key: str = ""
    api_key_env: str = "SILICONFLOW_API_KEY"
    model: str = "Qwen/Qwen3-Embedding-8B"
    timeout_sec: float = 8.0


@dataclass
class RerankConfig:
    enabled: bool = False
    base_url: str = "https://api.siliconflow.cn/v1"
    base_url_env: str = "SILICONFLOW_BASE_URL"
    api_key: str = ""
    api_key_env: str = "SILICONFLOW_API_KEY"
    model: str = "Qwen/Qwen3-Reranker-8B"
    timeout_sec: float = 12.0


@dataclass
class AppConfig:
    app_name: str = "WeChat"
    poll_interval_sec: float = 2.0
    action_cooldown_sec: float = 8.0
    normal_reply_interval_sec: float = 60.0
    dry_run: bool = True
    activate_wait_sec: float = 0.6
    click_move_duration_sec: float = 0.18
    mouse_down_hold_sec: float = 0.03
    post_select_wait_sec: float = 0.35
    focus_verify_enabled: bool = True
    focus_verify_max_clicks: int = 3
    focus_verify_wait_sec: float = 0.20
    trigger_on_preview_change: bool = True
    debug_scan: bool = False
    log_verbose: bool = False
    log_snapshot_rows: int = 6
    process_existing_unread_on_start: bool = True
    skip_first_action_on_start: bool = True
    memory_enabled: bool = True
    memory_store_path: str = "data/session_memory.json"
    memory_short_max_items: int = 12
    memory_short_context_items: int = 8
    memory_summary_update_every: int = 4
    memory_summary_recent_items: int = 10
    memory_summary_max_chars: int = 500
    memory_history_context_items: int = 24
    # 0 means unlimited.
    memory_history_max_items: int = 0
    workspace_enabled: bool = True
    workspace_dir: str = "agent_workspace"
    workspace_memory_main_only: bool = True
    workspace_memory_search_limit: int = 3
    workspace_memory_rerank_enabled: bool = False
    workspace_memory_rerank_shortlist: int = 24
    workspace_memory_rerank_weight: float = 2.5
    admin_commands_enabled: bool = True
    admin_session_titles: list[str] = field(default_factory=lambda: ["example_admin"])
    admin_command_prefix: str = "/"
    agent_actions_enabled: bool = True
    agent_actions_max_per_turn: int = 2
    agent_actions_fail_open: bool = True
    tavily_enabled: bool = False
    tavily_base_url: str = "https://api.tavily.com"
    tavily_api_key: str = ""
    tavily_api_key_env: str = "TAVILY_API_KEY"
    tavily_max_results: int = 3
    tavily_timeout_sec: float = 8.0
    heartbeat_enabled: bool = False
    heartbeat_interval_sec: float = 1800.0
    heartbeat_min_idle_sec: float = 20.0
    heartbeat_max_actions: int = 2
    heartbeat_fail_open: bool = True
    heartbeat_prompt: str = (
        "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
        "Do not infer or repeat old tasks from prior chats. "
        "If nothing needs attention, return no actions."
    )

    reply_on_new_message: str = "已收到消息，我稍后回复你。"
    reply_on_mention: str = "收到@，我看到了，稍后处理。"
    mention_keywords: list[str] = field(
        default_factory=lambda: ["@我", "有人@我", "[有人@我]"]
    )
    mention_any_at: bool = False
    group_title_prefixes: list[str] = field(default_factory=lambda: ["群"])
    group_detect_sender_prefix: bool = True
    group_require_sender_prefix_for_new_message: bool = True
    group_only_reply_when_mentioned: bool = True
    # Allow reply LLM to explicitly skip sending in group chats via [NO_REPLY].
    group_allow_llm_no_reply: bool = True
    group_reply_keywords: list[str] = field(
        default_factory=lambda: ["@助手", "@机器人", "机器人", "bot", "小助手"]
    )
    ignore_title_keywords: list[str] = field(default_factory=lambda: ["折叠的聊天"])
    use_manual_row_boxes: bool = False
    manual_row_boxes_path: str = "data/manual_row_boxes.json"
    row_title_region_enabled: bool = False
    # Row-local ratios with left-bottom anchor: x from left, y from bottom.
    row_title_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.24, y=0.52, w=0.58, h=0.42)
    )
    preview_region_enabled: bool = False
    # Row-local ratios with left-bottom anchor: x from left, y from bottom.
    preview_text_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.24, y=0.10, w=0.72, h=0.52)
    )

    list_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.065, y=0.12, w=0.325, h=0.82)
    )
    rows_max: int = 8
    row_height_ratio: float = 0.145
    chat_context_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.40, y=0.16, w=0.57, h=0.63)
    )
    chat_title_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.40, y=0.01, w=0.57, h=0.10)
    )
    chat_title_region_group_enabled: bool = False
    chat_title_region_private_enabled: bool = False
    chat_title_region_group: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.40, y=0.01, w=0.57, h=0.10)
    )
    chat_title_region_private: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.40, y=0.01, w=0.57, h=0.10)
    )
    chat_context_max_lines: int = 14
    chat_self_x_ratio: float = 0.62
    skip_if_latest_chat_from_self: bool = True
    # If false, self-latest skip applies to groups only.
    skip_if_latest_chat_from_self_private: bool = False

    input_point: PointRatio = field(default_factory=lambda: PointRatio(x=0.73, y=0.92))
    unread_badge: UnreadBadgeConfig = field(default_factory=UnreadBadgeConfig)
    unread_badge_circle: UnreadBadgeCircleConfig = field(
        default_factory=UnreadBadgeCircleConfig
    )
    ocr: OcrConfig = field(default_factory=OcrConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    llm_reply: LlmConfig = field(default_factory=LlmConfig)
    llm_decision: LlmConfig = field(default_factory=LlmConfig)
    llm_planner: LlmConfig = field(default_factory=LlmConfig)
    llm_summary: LlmConfig = field(default_factory=LlmConfig)
    llm_heartbeat: LlmConfig = field(default_factory=LlmConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)


def _load_region(data: dict, key: str, default: RegionRatio) -> RegionRatio:
    raw = data.get(key, {})
    return RegionRatio(
        x=float(raw.get("x", default.x)),
        y=float(raw.get("y", default.y)),
        w=float(raw.get("w", default.w)),
        h=float(raw.get("h", default.h)),
    )


def _load_point(data: dict, key: str, default: PointRatio) -> PointRatio:
    raw = data.get(key, {})
    return PointRatio(
        x=float(raw.get("x", default.x)),
        y=float(raw.get("y", default.y)),
    )


def _load_ollama_think(raw: object, default: str) -> str:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return "true" if raw else "false"
    return str(raw).strip()


def _load_openai_think_mode(raw: object, default: str = "default") -> str:
    value = str(raw if raw is not None else default).strip().lower()
    if value in ("default", "auto", ""):
        return "default"
    if value in ("on", "true", "1", "enable", "enabled"):
        return "on"
    if value in ("off", "false", "0", "disable", "disabled"):
        return "off"
    return "default"


def _load_sarcasm_level(raw: object, default: str = "low") -> str:
    value = str(raw if raw is not None else default).strip().lower()
    if value in ("off", "none", "0", "false", "disable", "disabled", "no", "关", "关闭"):
        return "off"
    if value in ("low", "1", "mild", "light", "轻", "轻度"):
        return "low"
    if value in ("medium", "mid", "2", "normal", "default", "auto", "中", "中等"):
        return "medium"
    if value in ("high", "3", "strong", "heavy", "高", "重"):
        return "high"
    return _load_sarcasm_level(default, "low") if value != default else "low"


def _load_llm_config(raw_obj: object, default: LlmConfig) -> LlmConfig:
    raw = raw_obj if isinstance(raw_obj, dict) else {}
    base_url = str(raw.get("base_url", default.base_url)).strip().rstrip("/")
    base_url_env = str(raw.get("base_url_env", default.base_url_env)).strip()
    if (not base_url) and base_url_env:
        base_url = str(os.getenv(base_url_env, "")).strip().rstrip("/")
    return LlmConfig(
        enabled=bool(raw.get("enabled", default.enabled)),
        base_url=base_url,
        base_url_env=base_url_env,
        api_key=str(raw.get("api_key", default.api_key)),
        api_key_env=str(raw.get("api_key_env", default.api_key_env)),
        model=str(raw.get("model", default.model)),
        ollama_native=bool(raw.get("ollama_native", default.ollama_native)),
        ollama_think=_load_ollama_think(
            raw.get("ollama_think", default.ollama_think),
            default.ollama_think,
        ),
        openai_compat_think_mode=_load_openai_think_mode(
            raw.get("openai_compat_think_mode", default.openai_compat_think_mode),
            default.openai_compat_think_mode,
        ),
        temperature=float(raw.get("temperature", default.temperature)),
        presence_penalty=float(raw.get("presence_penalty", default.presence_penalty)),
        frequency_penalty=float(raw.get("frequency_penalty", default.frequency_penalty)),
        max_tokens=int(raw.get("max_tokens", default.max_tokens)),
        timeout_sec=float(raw.get("timeout_sec", default.timeout_sec)),
        interest_hint=str(raw.get("interest_hint", default.interest_hint)),
        sarcasm_level=_load_sarcasm_level(
            raw.get("sarcasm_level", default.sarcasm_level),
            default.sarcasm_level,
        ),
        system_prompt=str(raw.get("system_prompt", default.system_prompt)),
        decision_enabled=bool(raw.get("decision_enabled", default.decision_enabled)),
        decision_on_group=bool(raw.get("decision_on_group", default.decision_on_group)),
        decision_on_private=bool(raw.get("decision_on_private", default.decision_on_private)),
        decision_fail_open=bool(raw.get("decision_fail_open", default.decision_fail_open)),
        decision_max_tokens=int(raw.get("decision_max_tokens", default.decision_max_tokens)),
        decision_read_chat_context=bool(
            raw.get("decision_read_chat_context", default.decision_read_chat_context)
        ),
        decision_system_prompt=str(
            raw.get("decision_system_prompt", default.decision_system_prompt)
        ),
        summary_enabled=bool(raw.get("summary_enabled", default.summary_enabled)),
        summary_max_tokens=int(raw.get("summary_max_tokens", default.summary_max_tokens)),
        summary_system_prompt=str(
            raw.get("summary_system_prompt", default.summary_system_prompt)
        ),
        anti_repeat_enabled=bool(raw.get("anti_repeat_enabled", default.anti_repeat_enabled)),
        anti_repeat_window=int(raw.get("anti_repeat_window", default.anti_repeat_window)),
        anti_repeat_similarity=float(
            raw.get("anti_repeat_similarity", default.anti_repeat_similarity)
        ),
        anti_repeat_retry=int(raw.get("anti_repeat_retry", default.anti_repeat_retry)),
        reasoning_exclude=bool(raw.get("reasoning_exclude", default.reasoning_exclude)),
        reasoning_effort=str(raw.get("reasoning_effort", default.reasoning_effort)),
        debug_log_payload=bool(raw.get("debug_log_payload", default.debug_log_payload)),
        debug_log_response=bool(raw.get("debug_log_response", default.debug_log_response)),
    )


def load_config(path: str | Path | None) -> AppConfig:
    cfg = AppConfig()
    if path is None:
        return cfg

    config_path = Path(path)
    if not config_path.exists():
        return cfg

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    cfg.app_name = str(data.get("app_name", cfg.app_name))
    cfg.poll_interval_sec = float(data.get("poll_interval_sec", cfg.poll_interval_sec))
    cfg.action_cooldown_sec = float(data.get("action_cooldown_sec", cfg.action_cooldown_sec))
    cfg.normal_reply_interval_sec = float(
        data.get("normal_reply_interval_sec", cfg.normal_reply_interval_sec)
    )
    cfg.dry_run = bool(data.get("dry_run", cfg.dry_run))
    cfg.activate_wait_sec = float(data.get("activate_wait_sec", cfg.activate_wait_sec))
    cfg.click_move_duration_sec = float(
        data.get("click_move_duration_sec", cfg.click_move_duration_sec)
    )
    cfg.mouse_down_hold_sec = float(data.get("mouse_down_hold_sec", cfg.mouse_down_hold_sec))
    cfg.post_select_wait_sec = float(data.get("post_select_wait_sec", cfg.post_select_wait_sec))
    cfg.focus_verify_enabled = bool(data.get("focus_verify_enabled", cfg.focus_verify_enabled))
    cfg.focus_verify_max_clicks = int(
        data.get("focus_verify_max_clicks", cfg.focus_verify_max_clicks)
    )
    cfg.focus_verify_wait_sec = float(
        data.get("focus_verify_wait_sec", cfg.focus_verify_wait_sec)
    )
    cfg.trigger_on_preview_change = bool(
        data.get("trigger_on_preview_change", cfg.trigger_on_preview_change)
    )
    cfg.debug_scan = bool(data.get("debug_scan", cfg.debug_scan))
    cfg.log_verbose = bool(data.get("log_verbose", cfg.log_verbose))
    cfg.log_snapshot_rows = int(data.get("log_snapshot_rows", cfg.log_snapshot_rows))
    cfg.process_existing_unread_on_start = bool(
        data.get("process_existing_unread_on_start", cfg.process_existing_unread_on_start)
    )
    cfg.skip_first_action_on_start = bool(
        data.get("skip_first_action_on_start", cfg.skip_first_action_on_start)
    )
    cfg.memory_enabled = bool(data.get("memory_enabled", cfg.memory_enabled))
    cfg.memory_store_path = str(data.get("memory_store_path", cfg.memory_store_path))
    cfg.memory_short_max_items = int(
        data.get("memory_short_max_items", cfg.memory_short_max_items)
    )
    cfg.memory_short_context_items = int(
        data.get("memory_short_context_items", cfg.memory_short_context_items)
    )
    cfg.memory_summary_update_every = int(
        data.get("memory_summary_update_every", cfg.memory_summary_update_every)
    )
    cfg.memory_summary_recent_items = int(
        data.get("memory_summary_recent_items", cfg.memory_summary_recent_items)
    )
    cfg.memory_summary_max_chars = int(
        data.get("memory_summary_max_chars", cfg.memory_summary_max_chars)
    )
    cfg.memory_history_context_items = int(
        data.get("memory_history_context_items", cfg.memory_history_context_items)
    )
    cfg.memory_history_max_items = int(
        data.get("memory_history_max_items", cfg.memory_history_max_items)
    )
    cfg.workspace_enabled = bool(data.get("workspace_enabled", cfg.workspace_enabled))
    cfg.workspace_dir = str(data.get("workspace_dir", cfg.workspace_dir))
    cfg.workspace_memory_main_only = bool(
        data.get("workspace_memory_main_only", cfg.workspace_memory_main_only)
    )
    cfg.workspace_memory_search_limit = int(
        data.get("workspace_memory_search_limit", cfg.workspace_memory_search_limit)
    )
    cfg.workspace_memory_rerank_enabled = bool(
        data.get("workspace_memory_rerank_enabled", cfg.workspace_memory_rerank_enabled)
    )
    cfg.workspace_memory_rerank_shortlist = int(
        data.get("workspace_memory_rerank_shortlist", cfg.workspace_memory_rerank_shortlist)
    )
    cfg.workspace_memory_rerank_weight = float(
        data.get("workspace_memory_rerank_weight", cfg.workspace_memory_rerank_weight)
    )
    if cfg.workspace_memory_rerank_shortlist < 1:
        cfg.workspace_memory_rerank_shortlist = 1
    if cfg.workspace_memory_rerank_weight < 0.0:
        cfg.workspace_memory_rerank_weight = 0.0
    cfg.admin_commands_enabled = bool(
        data.get("admin_commands_enabled", cfg.admin_commands_enabled)
    )
    admin_session_titles = data.get("admin_session_titles", cfg.admin_session_titles)
    if isinstance(admin_session_titles, list):
        cfg.admin_session_titles = [str(x) for x in admin_session_titles]
    cfg.admin_command_prefix = str(data.get("admin_command_prefix", cfg.admin_command_prefix))
    cfg.agent_actions_enabled = bool(
        data.get("agent_actions_enabled", cfg.agent_actions_enabled)
    )
    cfg.agent_actions_max_per_turn = int(
        data.get("agent_actions_max_per_turn", cfg.agent_actions_max_per_turn)
    )
    cfg.agent_actions_fail_open = bool(
        data.get("agent_actions_fail_open", cfg.agent_actions_fail_open)
    )
    cfg.tavily_enabled = bool(data.get("tavily_enabled", cfg.tavily_enabled))
    cfg.tavily_base_url = str(data.get("tavily_base_url", cfg.tavily_base_url)).rstrip("/")
    cfg.tavily_api_key = str(data.get("tavily_api_key", cfg.tavily_api_key))
    cfg.tavily_api_key_env = str(data.get("tavily_api_key_env", cfg.tavily_api_key_env))
    cfg.tavily_max_results = int(data.get("tavily_max_results", cfg.tavily_max_results))
    cfg.tavily_timeout_sec = float(data.get("tavily_timeout_sec", cfg.tavily_timeout_sec))
    cfg.heartbeat_enabled = bool(data.get("heartbeat_enabled", cfg.heartbeat_enabled))
    cfg.heartbeat_interval_sec = float(
        data.get("heartbeat_interval_sec", cfg.heartbeat_interval_sec)
    )
    cfg.heartbeat_min_idle_sec = float(
        data.get("heartbeat_min_idle_sec", cfg.heartbeat_min_idle_sec)
    )
    cfg.heartbeat_max_actions = int(
        data.get("heartbeat_max_actions", cfg.heartbeat_max_actions)
    )
    cfg.heartbeat_fail_open = bool(
        data.get("heartbeat_fail_open", cfg.heartbeat_fail_open)
    )
    cfg.heartbeat_prompt = str(data.get("heartbeat_prompt", cfg.heartbeat_prompt))

    cfg.reply_on_new_message = str(
        data.get("reply_on_new_message", cfg.reply_on_new_message)
    )
    cfg.reply_on_mention = str(data.get("reply_on_mention", cfg.reply_on_mention))

    mention_keywords = data.get("mention_keywords", cfg.mention_keywords)
    if isinstance(mention_keywords, list):
        cfg.mention_keywords = [str(x) for x in mention_keywords]
    cfg.mention_any_at = bool(data.get("mention_any_at", cfg.mention_any_at))
    group_title_prefixes = data.get("group_title_prefixes", cfg.group_title_prefixes)
    if isinstance(group_title_prefixes, list):
        cfg.group_title_prefixes = [str(x) for x in group_title_prefixes]
    cfg.group_detect_sender_prefix = bool(
        data.get("group_detect_sender_prefix", cfg.group_detect_sender_prefix)
    )
    cfg.group_require_sender_prefix_for_new_message = bool(
        data.get(
            "group_require_sender_prefix_for_new_message",
            cfg.group_require_sender_prefix_for_new_message,
        )
    )
    cfg.group_only_reply_when_mentioned = bool(
        data.get("group_only_reply_when_mentioned", cfg.group_only_reply_when_mentioned)
    )
    cfg.group_allow_llm_no_reply = bool(
        data.get("group_allow_llm_no_reply", cfg.group_allow_llm_no_reply)
    )
    group_reply_keywords = data.get("group_reply_keywords", cfg.group_reply_keywords)
    if isinstance(group_reply_keywords, list):
        cfg.group_reply_keywords = [str(x) for x in group_reply_keywords]
    ignore_title_keywords = data.get("ignore_title_keywords", cfg.ignore_title_keywords)
    if isinstance(ignore_title_keywords, list):
        cfg.ignore_title_keywords = [str(x) for x in ignore_title_keywords]
    cfg.use_manual_row_boxes = bool(
        data.get("use_manual_row_boxes", cfg.use_manual_row_boxes)
    )
    cfg.manual_row_boxes_path = str(
        data.get("manual_row_boxes_path", cfg.manual_row_boxes_path)
    )
    cfg.row_title_region_enabled = bool(
        data.get("row_title_region_enabled", cfg.row_title_region_enabled)
    )
    cfg.row_title_region = _load_region(
        data, "row_title_region", cfg.row_title_region
    )
    cfg.preview_region_enabled = bool(
        data.get("preview_region_enabled", cfg.preview_region_enabled)
    )
    cfg.preview_text_region = _load_region(
        data, "preview_text_region", cfg.preview_text_region
    )

    cfg.list_region = _load_region(data, "list_region", cfg.list_region)
    cfg.rows_max = int(data.get("rows_max", cfg.rows_max))
    cfg.row_height_ratio = float(data.get("row_height_ratio", cfg.row_height_ratio))
    cfg.chat_context_region = _load_region(data, "chat_context_region", cfg.chat_context_region)
    cfg.chat_title_region = _load_region(data, "chat_title_region", cfg.chat_title_region)
    cfg.chat_title_region_group_enabled = bool(
        data.get("chat_title_region_group_enabled", cfg.chat_title_region_group_enabled)
    )
    cfg.chat_title_region_private_enabled = bool(
        data.get("chat_title_region_private_enabled", cfg.chat_title_region_private_enabled)
    )
    if "chat_title_region_group" in data:
        cfg.chat_title_region_group = _load_region(
            data, "chat_title_region_group", cfg.chat_title_region_group
        )
    else:
        cfg.chat_title_region_group = cfg.chat_title_region
    if "chat_title_region_private" in data:
        cfg.chat_title_region_private = _load_region(
            data, "chat_title_region_private", cfg.chat_title_region_private
        )
    else:
        cfg.chat_title_region_private = cfg.chat_title_region
    cfg.chat_context_max_lines = int(data.get("chat_context_max_lines", cfg.chat_context_max_lines))
    cfg.chat_self_x_ratio = float(data.get("chat_self_x_ratio", cfg.chat_self_x_ratio))
    cfg.skip_if_latest_chat_from_self = bool(
        data.get("skip_if_latest_chat_from_self", cfg.skip_if_latest_chat_from_self)
    )
    cfg.skip_if_latest_chat_from_self_private = bool(
        data.get(
            "skip_if_latest_chat_from_self_private",
            cfg.skip_if_latest_chat_from_self_private,
        )
    )

    cfg.input_point = _load_point(data, "input_point", cfg.input_point)

    badge = data.get("unread_badge", {})
    cfg.unread_badge = UnreadBadgeConfig(
        min_blob_pixels=int(badge.get("min_blob_pixels", cfg.unread_badge.min_blob_pixels))
    )
    badge_circle = data.get("unread_badge_circle", {})
    cfg.unread_badge_circle = UnreadBadgeCircleConfig(
        enabled=bool(badge_circle.get("enabled", cfg.unread_badge_circle.enabled)),
        x=float(badge_circle.get("x", cfg.unread_badge_circle.x)),
        y=float(badge_circle.get("y", cfg.unread_badge_circle.y)),
        r=float(badge_circle.get("r", cfg.unread_badge_circle.r)),
    )
    ocr_raw = data.get("ocr", {})
    cfg.ocr = OcrConfig(
        backend=str(ocr_raw.get("backend", cfg.ocr.backend)).strip().lower()
        or cfg.ocr.backend,
        ab_compare_backend=str(
            ocr_raw.get("ab_compare_backend", cfg.ocr.ab_compare_backend)
        )
        .strip()
        .lower(),
        ab_compare_sample_rate=float(
            ocr_raw.get("ab_compare_sample_rate", cfg.ocr.ab_compare_sample_rate)
        ),
        ab_compare_max_text_len=int(
            ocr_raw.get("ab_compare_max_text_len", cfg.ocr.ab_compare_max_text_len)
        ),
        enhance=bool(ocr_raw.get("enhance", cfg.ocr.enhance)),
        target_short_side=int(ocr_raw.get("target_short_side", cfg.ocr.target_short_side)),
        max_upscale=float(ocr_raw.get("max_upscale", cfg.ocr.max_upscale)),
        paddle_lang=str(ocr_raw.get("paddle_lang", cfg.ocr.paddle_lang)).strip()
        or cfg.ocr.paddle_lang,
        paddle_use_angle_cls=bool(
            ocr_raw.get("paddle_use_angle_cls", cfg.ocr.paddle_use_angle_cls)
        ),
    )
    cfg.ocr.ab_compare_sample_rate = max(0.0, min(1.0, cfg.ocr.ab_compare_sample_rate))
    if cfg.ocr.ab_compare_max_text_len < 24:
        cfg.ocr.ab_compare_max_text_len = 24
    if cfg.ocr.target_short_side < 320:
        cfg.ocr.target_short_side = 320
    if cfg.ocr.max_upscale < 1.0:
        cfg.ocr.max_upscale = 1.0

    llm_raw = data.get("llm", {})
    cfg.llm = _load_llm_config(llm_raw, cfg.llm)
    cfg.llm_reply = _load_llm_config(data.get("llm_reply", {}), cfg.llm)
    cfg.llm_decision = _load_llm_config(data.get("llm_decision", {}), cfg.llm)
    cfg.llm_planner = _load_llm_config(data.get("llm_planner", {}), cfg.llm)
    cfg.llm_summary = _load_llm_config(data.get("llm_summary", {}), cfg.llm)
    cfg.llm_heartbeat = _load_llm_config(data.get("llm_heartbeat", {}), cfg.llm)
    vision_raw = data.get("vision", {})
    vision_base_url = str(vision_raw.get("base_url", cfg.vision.base_url)).strip().rstrip("/")
    vision_base_url_env = str(vision_raw.get("base_url_env", cfg.vision.base_url_env)).strip()
    if (not vision_base_url) and vision_base_url_env:
        vision_base_url = str(os.getenv(vision_base_url_env, "")).strip().rstrip("/")
    cfg.vision = VisionConfig(
        enabled=bool(vision_raw.get("enabled", cfg.vision.enabled)),
        base_url=vision_base_url,
        base_url_env=vision_base_url_env,
        api_key=str(vision_raw.get("api_key", cfg.vision.api_key)),
        api_key_env=str(vision_raw.get("api_key_env", cfg.vision.api_key_env)),
        model=str(vision_raw.get("model", cfg.vision.model)),
        ollama_native=bool(vision_raw.get("ollama_native", cfg.vision.ollama_native)),
        ollama_think=_load_ollama_think(
            vision_raw.get("ollama_think", cfg.vision.ollama_think),
            cfg.vision.ollama_think,
        ),
        openai_compat_think_mode=_load_openai_think_mode(
            vision_raw.get("openai_compat_think_mode", cfg.vision.openai_compat_think_mode),
            cfg.vision.openai_compat_think_mode,
        ),
        timeout_sec=float(vision_raw.get("timeout_sec", cfg.vision.timeout_sec)),
        max_tokens=int(vision_raw.get("max_tokens", cfg.vision.max_tokens)),
        response_format_json_object=bool(
            vision_raw.get(
                "response_format_json_object",
                cfg.vision.response_format_json_object,
            )
        ),
        fail_open=bool(vision_raw.get("fail_open", cfg.vision.fail_open)),
        reasoning_exclude=bool(
            vision_raw.get("reasoning_exclude", cfg.vision.reasoning_exclude)
        ),
        reasoning_effort=str(vision_raw.get("reasoning_effort", cfg.vision.reasoning_effort)),
        debug_log_payload=bool(
            vision_raw.get("debug_log_payload", cfg.vision.debug_log_payload)
        ),
        debug_log_response=bool(
            vision_raw.get("debug_log_response", cfg.vision.debug_log_response)
        ),
        system_prompt=str(vision_raw.get("system_prompt", cfg.vision.system_prompt)),
    )
    if not cfg.vision.base_url:
        cfg.vision.base_url = cfg.llm.base_url
    if not cfg.vision.api_key:
        cfg.vision.api_key = cfg.llm.api_key
    if not cfg.vision.model:
        cfg.vision.model = cfg.llm.model

    embedding_raw = data.get("embedding", {})
    embedding_base_url = str(embedding_raw.get("base_url", cfg.embedding.base_url)).strip().rstrip("/")
    embedding_base_url_env = str(
        embedding_raw.get("base_url_env", cfg.embedding.base_url_env)
    ).strip()
    if (not embedding_base_url) and embedding_base_url_env:
        embedding_base_url = str(os.getenv(embedding_base_url_env, "")).strip().rstrip("/")
    cfg.embedding = EmbeddingConfig(
        enabled=bool(embedding_raw.get("enabled", cfg.embedding.enabled)),
        base_url=embedding_base_url,
        base_url_env=embedding_base_url_env,
        api_key=str(embedding_raw.get("api_key", cfg.embedding.api_key)),
        api_key_env=str(embedding_raw.get("api_key_env", cfg.embedding.api_key_env)),
        model=str(embedding_raw.get("model", cfg.embedding.model)),
        timeout_sec=float(embedding_raw.get("timeout_sec", cfg.embedding.timeout_sec)),
    )
    if not cfg.embedding.base_url:
        cfg.embedding.base_url = "https://api.siliconflow.cn/v1"

    rerank_raw = data.get("rerank", {})
    rerank_base_url = str(rerank_raw.get("base_url", cfg.rerank.base_url)).strip().rstrip("/")
    rerank_base_url_env = str(
        rerank_raw.get("base_url_env", cfg.rerank.base_url_env)
    ).strip()
    if (not rerank_base_url) and rerank_base_url_env:
        rerank_base_url = str(os.getenv(rerank_base_url_env, "")).strip().rstrip("/")
    cfg.rerank = RerankConfig(
        enabled=bool(rerank_raw.get("enabled", cfg.rerank.enabled)),
        base_url=rerank_base_url,
        base_url_env=rerank_base_url_env,
        api_key=str(rerank_raw.get("api_key", cfg.rerank.api_key)),
        api_key_env=str(rerank_raw.get("api_key_env", cfg.rerank.api_key_env)),
        model=str(rerank_raw.get("model", cfg.rerank.model)),
        timeout_sec=float(rerank_raw.get("timeout_sec", cfg.rerank.timeout_sec)),
    )
    if not cfg.rerank.base_url:
        cfg.rerank.base_url = "https://api.siliconflow.cn/v1"
    if cfg.heartbeat_interval_sec < 5.0:
        cfg.heartbeat_interval_sec = 5.0
    if cfg.heartbeat_min_idle_sec < 0.0:
        cfg.heartbeat_min_idle_sec = 0.0
    if cfg.heartbeat_max_actions < 1:
        cfg.heartbeat_max_actions = 1

    return cfg

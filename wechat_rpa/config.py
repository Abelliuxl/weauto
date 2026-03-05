from __future__ import annotations

from dataclasses import dataclass, field
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
class LlmConfig:
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    presence_penalty: float = 0.2
    frequency_penalty: float = 0.2
    max_tokens: int = 180
    timeout_sec: float = 20.0
    interest_hint: str = ""
    system_prompt: str = (
        "你是微信助手。请基于给定会话标题和预览内容生成简短、礼貌、自然的中文回复，"
        "长度控制在1-2句，不要编造事实。"
    )
    decision_enabled: bool = True
    decision_on_group: bool = True
    decision_on_private: bool = False
    decision_fail_open: bool = False
    decision_max_tokens: int = 32
    decision_read_chat_context: bool = True
    decision_system_prompt: str = (
        "你是消息分流器。给定会话标题、预览和触发原因，判断是否值得自动回复。"
        "仅输出JSON，格式为: {\"decision\":\"reply|skip\",\"reason\":\"<=20字\"}。"
    )
    summary_enabled: bool = True
    summary_max_tokens: int = 160
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


@dataclass
class VisionConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    model: str = ""
    timeout_sec: float = 20.0
    max_tokens: int = 512
    fail_open: bool = True
    reasoning_exclude: bool = True
    reasoning_effort: str = "low"
    system_prompt: str = (
        "你是微信聊天截图解析器。请严格输出 JSON，schema=wechat_vision_v1；"
        "不要输出 markdown，不要输出额外解释。"
    )


@dataclass
class AppConfig:
    app_name: str = "WeChat"
    poll_interval_sec: float = 2.0
    action_cooldown_sec: float = 8.0
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
    admin_commands_enabled: bool = True
    admin_session_titles: list[str] = field(default_factory=lambda: ["real刘晓亮"])
    admin_command_prefix: str = "/"

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
    group_reply_keywords: list[str] = field(
        default_factory=lambda: ["@助手", "@机器人", "机器人", "bot", "小助手"]
    )
    ignore_title_keywords: list[str] = field(default_factory=lambda: ["折叠的聊天"])
    use_manual_row_boxes: bool = False
    manual_row_boxes_path: str = "data/manual_row_boxes.json"

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
    chat_context_max_lines: int = 14
    chat_self_x_ratio: float = 0.62
    skip_if_latest_chat_from_self: bool = True

    input_point: PointRatio = field(default_factory=lambda: PointRatio(x=0.73, y=0.92))
    unread_badge: UnreadBadgeConfig = field(default_factory=UnreadBadgeConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)


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
    cfg.admin_commands_enabled = bool(
        data.get("admin_commands_enabled", cfg.admin_commands_enabled)
    )
    admin_session_titles = data.get("admin_session_titles", cfg.admin_session_titles)
    if isinstance(admin_session_titles, list):
        cfg.admin_session_titles = [str(x) for x in admin_session_titles]
    cfg.admin_command_prefix = str(data.get("admin_command_prefix", cfg.admin_command_prefix))

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

    cfg.list_region = _load_region(data, "list_region", cfg.list_region)
    cfg.rows_max = int(data.get("rows_max", cfg.rows_max))
    cfg.row_height_ratio = float(data.get("row_height_ratio", cfg.row_height_ratio))
    cfg.chat_context_region = _load_region(data, "chat_context_region", cfg.chat_context_region)
    cfg.chat_title_region = _load_region(data, "chat_title_region", cfg.chat_title_region)
    cfg.chat_context_max_lines = int(data.get("chat_context_max_lines", cfg.chat_context_max_lines))
    cfg.chat_self_x_ratio = float(data.get("chat_self_x_ratio", cfg.chat_self_x_ratio))
    cfg.skip_if_latest_chat_from_self = bool(
        data.get("skip_if_latest_chat_from_self", cfg.skip_if_latest_chat_from_self)
    )

    cfg.input_point = _load_point(data, "input_point", cfg.input_point)

    badge = data.get("unread_badge", {})
    cfg.unread_badge = UnreadBadgeConfig(
        min_blob_pixels=int(badge.get("min_blob_pixels", cfg.unread_badge.min_blob_pixels))
    )

    llm_raw = data.get("llm", {})
    cfg.llm = LlmConfig(
        enabled=bool(llm_raw.get("enabled", cfg.llm.enabled)),
        base_url=str(llm_raw.get("base_url", cfg.llm.base_url)).rstrip("/"),
        api_key=str(llm_raw.get("api_key", cfg.llm.api_key)),
        api_key_env=str(llm_raw.get("api_key_env", cfg.llm.api_key_env)),
        model=str(llm_raw.get("model", cfg.llm.model)),
        temperature=float(llm_raw.get("temperature", cfg.llm.temperature)),
        presence_penalty=float(llm_raw.get("presence_penalty", cfg.llm.presence_penalty)),
        frequency_penalty=float(llm_raw.get("frequency_penalty", cfg.llm.frequency_penalty)),
        max_tokens=int(llm_raw.get("max_tokens", cfg.llm.max_tokens)),
        timeout_sec=float(llm_raw.get("timeout_sec", cfg.llm.timeout_sec)),
        interest_hint=str(llm_raw.get("interest_hint", cfg.llm.interest_hint)),
        system_prompt=str(llm_raw.get("system_prompt", cfg.llm.system_prompt)),
        decision_enabled=bool(llm_raw.get("decision_enabled", cfg.llm.decision_enabled)),
        decision_on_group=bool(llm_raw.get("decision_on_group", cfg.llm.decision_on_group)),
        decision_on_private=bool(
            llm_raw.get("decision_on_private", cfg.llm.decision_on_private)
        ),
        decision_fail_open=bool(llm_raw.get("decision_fail_open", cfg.llm.decision_fail_open)),
        decision_max_tokens=int(llm_raw.get("decision_max_tokens", cfg.llm.decision_max_tokens)),
        decision_read_chat_context=bool(
            llm_raw.get("decision_read_chat_context", cfg.llm.decision_read_chat_context)
        ),
        decision_system_prompt=str(
            llm_raw.get("decision_system_prompt", cfg.llm.decision_system_prompt)
        ),
        summary_enabled=bool(llm_raw.get("summary_enabled", cfg.llm.summary_enabled)),
        summary_max_tokens=int(llm_raw.get("summary_max_tokens", cfg.llm.summary_max_tokens)),
        summary_system_prompt=str(
            llm_raw.get("summary_system_prompt", cfg.llm.summary_system_prompt)
        ),
        anti_repeat_enabled=bool(
            llm_raw.get("anti_repeat_enabled", cfg.llm.anti_repeat_enabled)
        ),
        anti_repeat_window=int(llm_raw.get("anti_repeat_window", cfg.llm.anti_repeat_window)),
        anti_repeat_similarity=float(
            llm_raw.get("anti_repeat_similarity", cfg.llm.anti_repeat_similarity)
        ),
        anti_repeat_retry=int(llm_raw.get("anti_repeat_retry", cfg.llm.anti_repeat_retry)),
        reasoning_exclude=bool(llm_raw.get("reasoning_exclude", cfg.llm.reasoning_exclude)),
        reasoning_effort=str(llm_raw.get("reasoning_effort", cfg.llm.reasoning_effort)),
    )
    vision_raw = data.get("vision", {})
    cfg.vision = VisionConfig(
        enabled=bool(vision_raw.get("enabled", cfg.vision.enabled)),
        base_url=str(vision_raw.get("base_url", cfg.vision.base_url)).rstrip("/"),
        api_key=str(vision_raw.get("api_key", cfg.vision.api_key)),
        api_key_env=str(vision_raw.get("api_key_env", cfg.vision.api_key_env)),
        model=str(vision_raw.get("model", cfg.vision.model)),
        timeout_sec=float(vision_raw.get("timeout_sec", cfg.vision.timeout_sec)),
        max_tokens=int(vision_raw.get("max_tokens", cfg.vision.max_tokens)),
        fail_open=bool(vision_raw.get("fail_open", cfg.vision.fail_open)),
        reasoning_exclude=bool(
            vision_raw.get("reasoning_exclude", cfg.vision.reasoning_exclude)
        ),
        reasoning_effort=str(vision_raw.get("reasoning_effort", cfg.vision.reasoning_effort)),
        system_prompt=str(vision_raw.get("system_prompt", cfg.vision.system_prompt)),
    )
    if not cfg.vision.base_url:
        cfg.vision.base_url = cfg.llm.base_url
    if not cfg.vision.api_key:
        cfg.vision.api_key = cfg.llm.api_key
    if not cfg.vision.model:
        cfg.vision.model = cfg.llm.model

    return cfg

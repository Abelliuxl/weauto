from __future__ import annotations

import re
import shutil
import time
from typing import TYPE_CHECKING

from .python_sandbox import run_python_calculation

if TYPE_CHECKING:
    from .bot import WeChatGuiRpaBot
    from .detector import ChatRowState


class ActionProcessor:
    """Runs planner tool actions for a chat event."""

    def __init__(self, bot: "WeChatGuiRpaBot") -> None:
        self.bot = bot

    def execute_agent_actions(
        self,
        row: "ChatRowState",
        actions: list[dict] | None,
        *,
        is_admin: bool,
        max_actions_override: int | None = None,
    ) -> tuple[str, str]:
        bot = self.bot
        if not actions:
            return "", ""

        key = bot._session_key_for_row(row)
        if max_actions_override is None:
            max_actions = max(1, int(bot.cfg.agent_actions_max_per_turn))
        else:
            override = int(max_actions_override)
            if override <= 0:
                max_actions = max(1, len(actions))
            else:
                max_actions = max(1, override)
        traces: list[str] = []
        observations: list[str] = []

        for idx, action in enumerate(actions[:max_actions], start=1):
            if not isinstance(action, dict):
                continue
            tool = str(action.get("tool", "")).strip()
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            reason = str(action.get("reason", "")).strip()[:40]
            status = ""
            obs = ""
            ok = False
            action_started = time.monotonic()
            if bot.cfg.log_verbose:
                arg_preview = bot._action_args_preview(args, limit=96)
                print(
                    f"[agent] action-start row={row.row_idx:>2} "
                    f"step={idx:>2}/{max_actions:<2} "
                    f"tool={tool or '-':<20} "
                    f"args={bot._fit_col(arg_preview, max(20, bot._term_width() - 60))}"
                )
            try:
                if tool == "remember_long_term":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        note = re.sub(r"\s+", " ", str(args.get("note", "")).strip())[:200]
                        if not note:
                            status = "skip (empty note)"
                        else:
                            bot._workspace.append_long_term_memory(note)
                            status = "ok"
                            obs = f"长期记忆新增: {note}"
                            ok = True
                elif tool == "maintain_memory":
                    days_raw = args.get("days", 3)
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 3
                    done, detail = bot._heartbeat_maintain_memory(days=max(1, min(14, days)))
                    status = "ok" if done else detail
                    obs = detail if done else ""
                    ok = done
                elif tool == "write_memory":
                    name = bot.agent_memory.normalize_name(str(args.get("name", "core")))
                    content = str(args.get("content", "") or args.get("text", "")).strip()
                    if not content:
                        status = "skip (empty content)"
                    else:
                        bot.agent_memory.backup(name)
                        bot.agent_memory.write(name, content.rstrip() + "\n")
                        status = "ok"
                        obs = f"记忆已写入 data/memory/{name}.md ({len(content)} chars)"
                        ok = True
                elif tool == "read_impression":
                    name = re.sub(r"_[0-9a-f]{8}$", "", str(args.get("name", "")).strip())
                    name = bot._resolve_person_name(name) or name
                    if not name:
                        status = "skip (empty name)"
                    else:
                        content = bot.agent_people.read(name).strip()
                        status = "ok" if content else "ok (no-hit)"
                        obs = (
                            f"人物印象[{name}]:\n{content[:1600]}"
                            if content
                            else f"人物印象[{name}]不存在"
                        )
                        ok = True
                elif tool == "write_impression":
                    name = re.sub(r"_[0-9a-f]{8}$", "", str(args.get("name", "")).strip())
                    name = bot._resolve_person_name(name) or name
                    content = str(args.get("content", "") or args.get("text", "")).strip()
                    if not name:
                        status = "skip (empty name)"
                    elif not content:
                        status = "skip (empty content)"
                    else:
                        bot.agent_people.write(name, content.rstrip() + "\n")
                        status = "ok"
                        obs = f"人物印象已写入 data/people/{name}.md ({len(content)} chars)"
                        ok = True
                elif tool == "read_chat_history":
                    chat_title = re.sub(
                        r"\s+",
                        " ",
                        str(args.get("chat_title", "") or args.get("title", "")).strip(),
                    )[:80]
                    limit_raw = args.get("limit", 50)
                    try:
                        limit = int(limit_raw)
                    except Exception:
                        limit = 50
                    target_row = row
                    if chat_title and bot._title_key(chat_title) != bot._title_key(row.title):
                        target_row = type(row)(
                            row_idx=row.row_idx,
                            text=chat_title,
                            title=chat_title,
                            preview="",
                            has_mention=False,
                            has_unread_badge=False,
                            fingerprint=f"history-{chat_title}",
                            click_x_ratio=-1.0,
                            click_y_ratio=-1.0,
                        )
                    history = bot._build_session_history_text(
                        target_row,
                        max_items=max(1, min(100, limit)),
                    )
                    status = "ok" if history else "ok (empty)"
                    obs = f"聊天记录[{chat_title or row.title}]:\n{history or '无'}"[:1800]
                    ok = True
                elif tool == "run_python":
                    code = str(args.get("code", "") or args.get("expression", "")).strip()
                    if not code:
                        status = "skip (empty code)"
                    else:
                        result = run_python_calculation(code, timeout_sec=2.0, max_output_chars=4000)
                        status = "ok" if result.ok else "error"
                        obs = f"Python结果:\n{result.to_tool_text()}"[:1800]
                        ok = result.ok
                elif tool == "refine_persona_files":
                    done, detail = bot._heartbeat_refine_persona_files()
                    status = "ok" if done else detail
                    obs = detail if done else ""
                    ok = done
                elif tool == "remember_session_fact":
                    fact = re.sub(r"\s+", " ", str(args.get("fact", "")).strip())[:120]
                    if not fact:
                        status = "skip (empty fact)"
                    else:
                        bot._workspace.remember_structured(
                            session_key=key,
                            title=row.title,
                            facts=[fact],
                        )
                        status = "ok"
                        obs = f"会话事实已记录: {fact}"
                        ok = True
                elif tool == "remember_session_event":
                    event = re.sub(r"\s+", " ", str(args.get("event", "")).strip())[:120]
                    if not event:
                        status = "skip (empty event)"
                    else:
                        bot._workspace.remember_structured(
                            session_key=key,
                            title=row.title,
                            events=[event],
                        )
                        status = "ok"
                        obs = f"会话事件已记录: {event}"
                        ok = True
                elif tool == "set_session_summary":
                    summary = re.sub(r"\s+", " ", str(args.get("summary", "")).strip())[:200]
                    if not summary:
                        status = "skip (empty summary)"
                    else:
                        bot._apply_session_summary(row, summary)
                        status = "ok"
                        obs = f"会话摘要已更新: {summary}"
                        ok = True
                elif tool == "search_memory":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    if not query:
                        status = "skip (empty query)"
                    else:
                        include_global = is_admin or (not bot.cfg.workspace_memory_main_only)
                        hits = bot._workspace.search_memory(
                            query=query,
                            session_key=key,
                            include_global=include_global,
                            limit=max(1, int(bot.cfg.workspace_memory_search_limit)),
                        )
                        compact = re.sub(r"\s+", " ", hits or "").strip()
                        if compact:
                            compact = compact[:260]
                            status = "ok"
                            obs = f"记忆检索[{query}]: {compact}"
                        else:
                            status = "ok (no-hit)"
                            obs = f"记忆检索[{query}]无命中"
                        ok = True
                elif tool == "search_person_impression":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    if not query:
                        status = "skip (empty query)"
                    elif not bot.cfg.person_impression_enabled:
                        status = "skip (person impression disabled)"
                    else:
                        hits = bot._workspace.search_person_impressions(
                            query=query,
                            limit=max(1, int(bot.cfg.person_impression_search_limit)),
                        )
                        compact = re.sub(r"\s+", " ", hits or "").strip()
                        if compact:
                            compact = compact[:260]
                            status = "ok"
                            obs = f"人物印象检索[{query}]: {compact}"
                        else:
                            status = "ok (no-hit)"
                            obs = f"人物印象检索[{query}]无命中"
                        ok = True
                elif tool == "maintain_person_impressions":
                    days_raw = args.get("days", bot.cfg.person_impression_days)
                    max_people_raw = args.get(
                        "max_people",
                        bot.cfg.person_impression_max_people_per_run,
                    )
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = int(bot.cfg.person_impression_days)
                    try:
                        max_people = int(max_people_raw)
                    except Exception:
                        max_people = int(bot.cfg.person_impression_max_people_per_run)
                    done, detail = bot._heartbeat_maintain_person_impressions(
                        days=max(1, min(3650, days)),
                        max_people=max(1, min(200, max_people)),
                    )
                    status = "ok" if done else detail
                    obs = detail if done else ""
                    ok = done
                elif tool == "workspace_list_files":
                    rel_path = re.sub(r"\s+", " ", str(args.get("path", "")).strip())[:200]
                    recursive_raw = args.get("recursive", False)
                    if isinstance(recursive_raw, str):
                        recursive = recursive_raw.strip().lower() in {"1", "true", "yes", "on"}
                    else:
                        recursive = bool(recursive_raw)
                    max_entries_raw = args.get("max_entries", 80)
                    try:
                        max_entries = int(max_entries_raw)
                    except Exception:
                        max_entries = 80
                    max_entries = max(1, min(200, max_entries))
                    listing = bot._workspace_list_files(
                        rel_path=rel_path,
                        recursive=recursive,
                        max_entries=max_entries,
                    )
                    status = "ok"
                    obs = (
                        f"工作区目录[{rel_path or '.'}] "
                        f"(recursive={recursive}, max={max_entries}):\n{listing}"
                    )[:1800]
                    ok = True
                elif tool == "workspace_read_file":
                    rel_path = re.sub(
                        r"\s+",
                        " ",
                        str(args.get("path", "") or args.get("file", "")).strip(),
                    )[:200]
                    if not rel_path:
                        status = "skip (empty path)"
                    else:
                        text = bot._workspace_read_file(rel_path=rel_path, max_chars=4000)
                        status = "ok"
                        obs = f"工作区文件读取[{rel_path}]:\n{text}"[:1800]
                        ok = True
                elif tool == "workspace_write_file":
                    rel_path = re.sub(
                        r"\s+",
                        " ",
                        str(args.get("path", "") or args.get("file", "")).strip(),
                    )[:200]
                    content = str(args.get("content", "") or args.get("text", "")).strip()[:4000]
                    mode_raw = str(args.get("mode", "overwrite")).strip().lower()
                    mode = "append" if mode_raw in {"append", "a", "追加"} else "overwrite"
                    if not rel_path:
                        status = "skip (empty path)"
                    elif not content:
                        status = "skip (empty content)"
                    else:
                        detail = bot._workspace_write_file(
                            rel_path=rel_path,
                            content=content,
                            mode=mode,
                        )
                        status = "ok"
                        obs = f"工作区写入成功: {detail}"
                        ok = True
                elif tool == "web_search":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    provider = bot._active_web_search_provider()
                    if not query:
                        status = "skip (empty query)"
                    elif not bot._web_search_enabled(provider):
                        status = f"skip ({provider} disabled)"
                    elif (provider in ("tavily", "brave")) and (
                        not bot._resolve_web_search_api_key(provider)
                    ):
                        status = f"skip (missing {provider} api key)"
                    elif provider == "agent_reach" and (
                        not str(bot.cfg.agent_reach_mcporter_cmd or "").strip()
                        or not shutil.which(str(bot.cfg.agent_reach_mcporter_cmd or "").strip())
                    ):
                        cmd = str(bot.cfg.agent_reach_mcporter_cmd or "").strip()
                        status = f"skip (missing command: {cmd or 'mcporter'})"
                    else:
                        active_provider, search_text = bot._web_search(query)
                        if search_text:
                            status = "ok"
                            obs = f"网页检索[{query}]({active_provider}): {search_text}"
                        else:
                            status = "ok (no-hit)"
                            obs = f"网页检索[{query}]({active_provider})无命中"
                        ok = True
                elif tool == "web_search_volc":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    if not query:
                        status = "skip (empty query)"
                    elif not bool(bot.cfg.volc_ark_enabled):
                        status = "skip (volc_ark disabled)"
                    elif not str(bot.cfg.volc_ark_model or "").strip():
                        status = "skip (missing volc_ark_model)"
                    elif not bot._resolve_volc_ark_api_key():
                        status = "skip (missing volc_ark api key)"
                    else:
                        search_text = bot._volc_web_search(query)
                        if search_text:
                            status = "ok"
                            obs = f"网页检索[{query}](volc_ark): {search_text}"
                            summary_line = ""
                            for line in str(search_text).splitlines():
                                clean_line = bot._compact_web_text(line, limit=120)
                                if not clean_line:
                                    continue
                                if clean_line.startswith("摘要:"):
                                    summary_line = bot._compact_web_text(
                                        clean_line.replace("摘要:", "", 1),
                                        limit=90,
                                    )
                                    break
                                if not summary_line:
                                    summary_line = clean_line
                            fact = bot._compact_web_text(
                                f"{query}：{summary_line}",
                                limit=120,
                            )
                            if fact:
                                bot._workspace.remember_structured(
                                    session_key=key,
                                    title=row.title,
                                    facts=[fact],
                                )
                        else:
                            status = "ok (no-hit)"
                            obs = f"网页检索[{query}](volc_ark)无命中"
                        ok = True
                elif tool == "generate_image":
                    prompt = bot._clean_image_prompt(args.get("prompt", ""), limit=280)
                    if not prompt:
                        status = "skip (empty prompt)"
                    elif not bot._has_image_generation_tool():
                        status = f"skip ({bot._image_generation_status_text()})"
                    else:
                        requested_size = bot._normalize_image_size(args.get("size", ""))
                        image_path = bot._generate_image_file(
                            prompt=prompt,
                            size=requested_size,
                        )
                        sent_ok = bot._send_generated_file(row, image_path)
                        compact_prompt = bot._compact_web_text(prompt, limit=52)
                        if sent_ok:
                            status = "ok"
                            obs = (
                                f"已生成并发送图片文件[{requested_size}]: "
                                f"{image_path.name} (prompt={compact_prompt})"
                            )
                        else:
                            status = "ok (generated, send-failed)"
                            obs = (
                                f"图片已生成但发送失败: {image_path.name} "
                                f"(prompt={compact_prompt})"
                            )
                        ok = True
                elif tool == "edit_image":
                    prompt = bot._clean_image_prompt(args.get("prompt", ""), limit=800)
                    image_path = str(args.get("image_path", "") or args.get("path", "")).strip()
                    image_url = str(args.get("image_url", "") or args.get("url", "")).strip()
                    if not image_path and not image_url:
                        image_path = bot._latest_image_for_row(row)
                    if not prompt:
                        status = "skip (empty prompt)"
                    elif not bot._has_image_editing_tool():
                        status = f"skip ({bot._image_editing_status_text()})"
                    elif not (image_path or image_url):
                        status = "skip (missing source image)"
                    else:
                        requested_size = bot._normalize_image_size(args.get("size", ""))
                        edited_path = bot._generate_edited_image_file(
                            prompt=prompt,
                            image_path=image_path,
                            image_url=image_url,
                            size=requested_size,
                        )
                        sent_ok = bot._send_generated_file(row, edited_path)
                        compact_prompt = bot._compact_web_text(prompt, limit=52)
                        if sent_ok:
                            status = "ok"
                            obs = (
                                f"已编辑并发送图片文件[{requested_size}]: "
                                f"{edited_path.name} (prompt={compact_prompt})"
                            )
                        else:
                            status = "ok (edited, send-failed)"
                            obs = (
                                f"图片已编辑但发送失败: {edited_path.name} "
                                f"(prompt={compact_prompt})"
                            )
                        ok = True
                elif tool == "mute_session":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        sess = bot._get_or_create_session(key)
                        sess.muted = True
                        bot._memory_dirty = True
                        status = "ok"
                        obs = "当前会话已静音"
                        ok = True
                elif tool == "unmute_session":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        sess = bot._get_or_create_session(key)
                        sess.muted = False
                        bot._memory_dirty = True
                        status = "ok"
                        obs = "当前会话已取消静音"
                        ok = True
                else:
                    status = "skip (unknown tool)"
            except Exception as exc:
                err = bot._compact_web_text(exc, limit=240)
                status = f"error ({err})"
                if tool in ("web_search", "web_search_volc"):
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    obs = f"网页检索[{query}]失败: {err}"
                elif tool == "search_memory":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    obs = f"记忆检索[{query}]失败: {err}"
                elif tool == "search_person_impression":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    obs = f"人物印象检索[{query}]失败: {err}"
                elif tool == "maintain_person_impressions":
                    obs = f"人物印象维护失败: {err}"
                elif tool == "write_memory":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"记忆写入[{name}]失败: {err}"
                elif tool == "read_impression":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"人物印象读取[{name}]失败: {err}"
                elif tool == "write_impression":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"人物印象写入[{name}]失败: {err}"
                elif tool == "read_chat_history":
                    chat_title = re.sub(r"\s+", " ", str(args.get("chat_title", "")).strip())[:80]
                    obs = f"聊天记录读取[{chat_title or row.title}]失败: {err}"
                elif tool == "run_python":
                    obs = f"Python执行失败: {err}"
                elif tool == "workspace_list_files":
                    rel_path = re.sub(r"\s+", " ", str(args.get("path", "")).strip())[:120]
                    obs = f"工作区目录[{rel_path or '.'}]失败: {err}"
                elif tool == "workspace_read_file":
                    rel_path = re.sub(r"\s+", " ", str(args.get("path", "")).strip())[:120]
                    obs = f"工作区读取[{rel_path}]失败: {err}"
                elif tool == "workspace_write_file":
                    rel_path = re.sub(r"\s+", " ", str(args.get("path", "")).strip())[:120]
                    obs = f"工作区写入[{rel_path}]失败: {err}"
                elif tool == "generate_image":
                    prompt = bot._clean_image_prompt(args.get("prompt", ""), limit=60)
                    obs = f"图片生成[{prompt}]失败: {err}"
                elif tool == "edit_image":
                    prompt = bot._clean_image_prompt(args.get("prompt", ""), limit=60)
                    obs = f"图片编辑[{prompt}]失败: {err}"
            action_elapsed = time.monotonic() - action_started
            if bot.cfg.log_verbose:
                print(
                    f"[agent] action-done  row={row.row_idx:>2} "
                    f"step={idx:>2}/{max_actions:<2} "
                    f"tool={tool or '-':<20} "
                    f"elapsed={action_elapsed:>5.2f}s status={status}"
                )

            trace = f"{idx}. {tool or '-'} -> {status}"
            if reason:
                trace += f" | {reason}"
            traces.append(trace)
            if obs:
                observations.append(obs)
            heartbeat_internal_tool = (
                row.title == "__heartbeat__"
                and tool in {"read_impression", "write_impression", "write_memory"}
            )
            if ok and not heartbeat_internal_tool:
                bot._append_session_record(
                    row,
                    role="assistant",
                    text=f"[tool:{tool}] {obs or status}",
                    content_type="text",
                    sender="",
                    source="tool",
                    count_turn=False,
                )

        return "\n".join(traces)[:1500], "\n".join(observations)[:2200]

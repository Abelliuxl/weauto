from __future__ import annotations

import re
import shutil
import time
import urllib.parse
from typing import TYPE_CHECKING

from .python_sandbox import run_python_calculation

if TYPE_CHECKING:
    from .bot import WeChatGuiRpaBot
    from .detector import ChatRowState


def _url_host_matches(url: str, domain: str) -> bool:
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    clean_domain = domain.lower().strip().rstrip(".")
    return bool(host and clean_domain and (host == clean_domain or host.endswith(f".{clean_domain}")))


def _compact_note(raw: object, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit]


def _append_unique_note(existing: str, *, heading: str, bullet: str) -> tuple[str, bool]:
    clean_bullet = bullet.strip()
    if not clean_bullet:
        return existing.rstrip() + ("\n" if existing.strip() else ""), False
    normalized_existing = re.sub(r"\s+", " ", existing or "").strip()
    normalized_note = re.sub(r"\s+", " ", clean_bullet.lstrip("- ").strip())
    if normalized_note and normalized_note in normalized_existing:
        return existing.rstrip() + ("\n" if existing.strip() else ""), False
    body = (existing or "").rstrip()
    if body:
        body += "\n\n"
    body += f"{heading}\n{clean_bullet}\n"
    return body, True


def _memory_snippets(*, label: str, content: str, query_terms: list[str], limit: int) -> list[str]:
    rows: list[str] = []
    for raw_line in (content or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        hay = line.lower()
        if not query_terms or any(term in hay for term in query_terms):
            rows.append(f"[{label}] {line[:220]}")
        if len(rows) >= limit:
            break
    return rows


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
                if tool == "read_memory":
                    raw_name = str(args.get("name", "") or args.get("scope", "") or "all").strip().lower()
                    names = ["core", "timeline"] if raw_name in {"", "all", "*"} else [bot.agent_memory.normalize_name(raw_name)]
                    parts: list[str] = []
                    for name in names:
                        content = bot.agent_memory.read(name).strip()
                        parts.append(f"记忆[{name}]:\n{content[:2000] if content else '无'}")
                    status = "ok"
                    obs = "\n\n".join(parts)[:2600]
                    ok = True
                elif tool == "recall_memory":
                    query = _compact_note(args.get("query", "") or args.get("text", ""), limit=80)
                    limit_raw = args.get("limit", 6)
                    try:
                        limit = int(limit_raw)
                    except Exception:
                        limit = 6
                    limit = max(1, min(10, limit))
                    if not query:
                        status = "skip (empty query)"
                    else:
                        terms = [
                            term.lower()
                            for term in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", query)
                            if len(term.strip()) >= 2
                        ][:8]
                        rows: list[str] = []
                        rows.extend(_memory_snippets(label="core", content=bot.agent_memory.read("core"), query_terms=terms, limit=limit))
                        if len(rows) < limit:
                            rows.extend(_memory_snippets(label="timeline", content=bot.agent_memory.read("timeline"), query_terms=terms, limit=limit - len(rows)))
                        if len(rows) < limit:
                            for person in bot.agent_people.list():
                                if len(rows) >= limit:
                                    break
                                content = bot.agent_people.read(person)
                                snippets = _memory_snippets(
                                    label=f"people/{person}",
                                    content=content,
                                    query_terms=terms,
                                    limit=limit - len(rows),
                                )
                                if snippets or any(term in person.lower() for term in terms):
                                    rows.extend(snippets or [f"[people/{person}] {content.strip()[:220] or '空记录'}"])
                        status = "ok" if rows else "ok (no-hit)"
                        obs = "记忆检索结果:\n" + ("\n".join(rows) if rows else "无")
                        ok = True
                elif tool == "remember_fact":
                    raw_scope = str(args.get("scope", "") or args.get("name", "") or "core").strip().lower()
                    name = "timeline" if raw_scope in {"timeline", "time", "history", "events"} else "core"
                    content = _compact_note(args.get("content", "") or args.get("fact", "") or args.get("text", ""), limit=1200)
                    source = _compact_note(args.get("source", ""), limit=80)
                    if not content:
                        status = "skip (empty content)"
                    else:
                        bot.agent_memory.backup(name)
                        existing = bot.agent_memory.read(name)
                        date = time.strftime("%Y-%m-%d")
                        if name == "timeline":
                            note = f"- {date}: {content}"
                        else:
                            note = f"- {content}"
                        if source:
                            note += f"（来源：{source}）"
                        merged, changed = _append_unique_note(existing, heading="## 追加记忆", bullet=note)
                        if changed:
                            bot.agent_memory.write(name, merged)
                            status = "ok"
                            obs = f"记忆已追加 data/memory/{name}.md: {content[:120]}"
                        else:
                            status = "ok (duplicate)"
                            obs = f"记忆已存在 data/memory/{name}.md: {content[:120]}"
                        ok = True
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
                elif tool == "write_skill":
                    name = bot.agent_skills.normalize_name(str(args.get("name", "") or args.get("skill", "")))
                    content = str(args.get("content", "") or args.get("text", "")).strip()
                    if not name:
                        status = "skip (empty name)"
                    elif not content:
                        status = "skip (empty content)"
                    else:
                        bot.agent_skills.backup(name)
                        bot.agent_skills.write(name, content.rstrip() + "\n")
                        status = "ok"
                        obs = f"技能已写入 data/skills/{name}/SKILL.md ({len(content)} chars)"
                        ok = True
                elif tool == "list_skills":
                    from wechat_rpa.prompt_context import list_skills as _list_skills

                    metas = _list_skills()
                    if not metas:
                        status = "ok (empty)"
                        obs = "当前没有已注册的技能"
                    else:
                        lines: list[str] = []
                        for m in metas:
                            name = str(m.get("name", "") or "?")
                            summary = str(m.get("summary", "") or "无摘要")
                            keywords = ", ".join(str(k) for k in (m.get("keywords") or []) if k)
                            line = f"- {name}: {summary}"
                            if keywords:
                                line += f" ({keywords})"
                            lines.append(line)
                        obs = f"技能列表 ({len(metas)} 个):\n" + "\n".join(lines)
                        status = "ok"
                        ok = True
                elif tool == "read_skill":
                    name = bot.agent_skills.normalize_name(str(args.get("name", "") or args.get("skill", "")))
                    if not name:
                        status = "skip (empty name)"
                    else:
                        content = bot.agent_skills.read(name).strip()
                        status = "ok" if content else "ok (no-hit)"
                        obs = f"技能[{name}]:\n{content[:5000] if content else '无'}"
                        ok = True
                elif tool == "update_skill":
                    name = bot.agent_skills.normalize_name(str(args.get("name", "") or args.get("skill", "")))
                    note = _compact_note(
                        args.get("note", "") or args.get("content", "") or args.get("text", ""),
                        limit=1800,
                    )
                    source = _compact_note(args.get("source", ""), limit=80)
                    if not name:
                        status = "skip (empty name)"
                    elif not note:
                        status = "skip (empty content)"
                    else:
                        existing = bot.agent_skills.read(name)
                        bullet = f"- {note}"
                        if source:
                            bullet += f"（来源：{source}）"
                        merged, changed = _append_unique_note(existing, heading="## 追加维护", bullet=bullet)
                        if changed:
                            bot.agent_skills.backup(name)
                            bot.agent_skills.write(name, merged)
                            status = "ok"
                            obs = f"技能已追加 data/skills/{name}/SKILL.md: {note[:120]}"
                        else:
                            status = "ok (duplicate)"
                            obs = f"技能内容已存在 data/skills/{name}/SKILL.md: {note[:120]}"
                        ok = True
                elif tool == "delete_skill":
                    name = bot.agent_skills.normalize_name(str(args.get("name", "") or args.get("skill", "")))
                    if not name:
                        status = "skip (empty name)"
                    else:
                        bot.agent_skills.delete(name)
                        status = "ok"
                        obs = f"技能已删除 data/skills/{name}"
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
                elif tool == "update_impression":
                    name = re.sub(r"_[0-9a-f]{8}$", "", str(args.get("name", "")).strip())
                    name = bot._resolve_person_name(name) or name
                    note = _compact_note(args.get("note", "") or args.get("content", "") or args.get("text", ""), limit=1200)
                    source = _compact_note(args.get("source", ""), limit=80)
                    if not name:
                        status = "skip (empty name)"
                    elif not note:
                        status = "skip (empty note)"
                    else:
                        existing = bot.agent_people.read(name).strip()
                        date = time.strftime("%Y-%m-%d")
                        bullet = f"- {date}: {note}"
                        if source:
                            bullet += f"（来源：{source}）"
                        base = existing or f"# {name}\n"
                        merged, changed = _append_unique_note(base, heading="## 补充观察", bullet=bullet)
                        if changed:
                            bot.agent_people.write(name, merged)
                            status = "ok"
                            obs = f"人物印象已更新 data/people/{name}.md: {note[:120]}"
                        else:
                            status = "ok (duplicate)"
                            obs = f"人物印象已存在 data/people/{name}.md: {note[:120]}"
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
                        result = run_python_calculation(
                            code, timeout_sec=2.0, max_output_chars=4000,
                            restricted=bot.cfg.python_sandbox_restricted,
                        )
                        status = "ok" if result.ok else "error"
                        obs = f"Python结果:\n{result.to_tool_text()}"[:1800]
                        ok = result.ok
                elif tool in {"web_search", "search_web", "search_web_brave"}:
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    provider = (
                        "tavily"
                        if tool == "search_web"
                        else ("brave" if tool == "search_web_brave" else bot._active_web_search_provider())
                    )
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
                        active_provider, search_text = bot._web_search_with_provider(provider, query)
                        if search_text:
                            status = "ok"
                            obs = f"网页检索[{query}]({active_provider}): {search_text}"[:1800]
                        else:
                            status = "ok (no-hit)"
                            obs = f"网页检索[{query}]({active_provider})无命中"
                        ok = True
                elif tool in {"web_search_volc", "search_web_volc"}:
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
                        else:
                            status = "ok (no-hit)"
                            obs = f"网页检索[{query}](volc_ark)无命中"
                        ok = True
                elif tool == "fetch_url":
                    url = re.sub(r"\s+", "", str(args.get("url", "")).strip())[:1000]
                    proxy_raw = args.get("proxy", True)
                    use_proxy = (
                        proxy_raw.strip().lower() in {"1", "true", "yes", "on"}
                        if isinstance(proxy_raw, str)
                        else bool(proxy_raw)
                    )
                    if not url:
                        status = "skip (empty url)"
                    elif _url_host_matches(url, "wowhead.com"):
                        text = bot._browse_url(url, max_chars=10000, use_proxy=use_proxy)
                        status = "ok (auto browse_url)" if text else "ok (empty; auto browse_url)"
                        obs = f"网页浏览[{url}](fetch_url自动改用browse_url):\n{text or '无内容'}"[:1800]
                        ok = True
                    else:
                        try:
                            text = bot._fetch_url(url, max_chars=6000, use_proxy=use_proxy)
                            status = "ok" if text else "ok (empty)"
                            obs = f"网页抓取[{url}]:\n{text or '无内容'}"[:1800]
                        except Exception as exc:
                            err_text = str(exc)
                            if "403" not in err_text:
                                raise
                            text = bot._browse_url(url, max_chars=10000, use_proxy=use_proxy)
                            status = "ok (403 fallback browse_url)" if text else "ok (empty; 403 fallback browse_url)"
                            obs = f"网页浏览[{url}](fetch_url 403 后自动改用 browse_url):\n{text or '无内容'}"[:1800]
                        ok = True
                elif tool == "browse_url":
                    url = re.sub(r"\s+", "", str(args.get("url", "")).strip())[:1000]
                    proxy_raw = args.get("proxy", True)
                    use_proxy = (
                        proxy_raw.strip().lower() in {"1", "true", "yes", "on"}
                        if isinstance(proxy_raw, str)
                        else bool(proxy_raw)
                    )
                    if not url:
                        status = "skip (empty url)"
                    else:
                        text = bot._browse_url(url, max_chars=10000, use_proxy=use_proxy)
                        status = "ok" if text else "ok (empty)"
                        obs = f"网页浏览[{url}]:\n{text or '无内容'}"[:1800]
                        ok = True
                elif tool == "build_wow_character_url":
                    result = bot._build_wow_character_url(args)
                    status = "ok" if result.get("ok") else "error"
                    obs = f"魔兽角色链接:\n{bot._format_wow_character_result(result)}"[:1800]
                    ok = bool(result.get("ok"))
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
                if tool in ("web_search", "search_web", "search_web_brave", "web_search_volc", "search_web_volc"):
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    obs = f"网页检索[{query}]失败: {err}"
                elif tool in ("fetch_url", "browse_url"):
                    url = re.sub(r"\s+", "", str(args.get("url", "")).strip())[:120]
                    obs = f"网页读取[{url}]失败: {err}"
                elif tool == "write_memory":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"记忆写入[{name}]失败: {err}"
                elif tool == "read_memory":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"记忆读取[{name or 'all'}]失败: {err}"
                elif tool == "remember_fact":
                    name = re.sub(r"\s+", " ", str(args.get("scope", "")).strip())[:40]
                    obs = f"记忆追加[{name or 'core'}]失败: {err}"
                elif tool == "recall_memory":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    obs = f"记忆检索[{query}]失败: {err}"
                elif tool == "write_skill":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:80]
                    obs = f"技能写入[{name}]失败: {err}"
                elif tool == "read_skill":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:80]
                    obs = f"技能读取[{name}]失败: {err}"
                elif tool == "update_skill":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:80]
                    obs = f"技能更新[{name}]失败: {err}"
                elif tool == "list_skills":
                    obs = f"技能列表获取失败: {err}"
                elif tool == "delete_skill":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:80]
                    obs = f"技能删除[{name}]失败: {err}"
                elif tool == "read_impression":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"人物印象读取[{name}]失败: {err}"
                elif tool == "update_impression":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"人物印象更新[{name}]失败: {err}"
                elif tool == "write_impression":
                    name = re.sub(r"\s+", " ", str(args.get("name", "")).strip())[:40]
                    obs = f"人物印象写入[{name}]失败: {err}"
                elif tool == "read_chat_history":
                    chat_title = re.sub(r"\s+", " ", str(args.get("chat_title", "")).strip())[:80]
                    obs = f"聊天记录读取[{chat_title or row.title}]失败: {err}"
                elif tool == "run_python":
                    obs = f"Python执行失败: {err}"
                elif tool == "build_wow_character_url":
                    obs = f"魔兽角色链接构建失败: {err}"
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
                (row.row_idx < 0 or row.title == "__heartbeat__")
                and tool in {
                    "read_memory",
                    "recall_memory",
                    "remember_fact",
                    "read_impression",
                    "update_impression",
                    "write_impression",
                    "write_memory",
                    "read_skill",
                    "update_skill",
                    "write_skill",
                    "delete_skill",
                    "list_skills",
                    "read_chat_history",
                }
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

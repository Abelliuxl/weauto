from __future__ import annotations

import re
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import WeChatGuiRpaBot
    from .detector import ChatRowState


class MessageHandler:
    """Handles one picked chat event after the receiver loop detects it."""

    def __init__(self, bot: "WeChatGuiRpaBot") -> None:
        self.bot = bot

    @staticmethod
    def _empty_context_snapshot() -> SimpleNamespace:
        return SimpleNamespace(
            text="",
            last_side="unknown",
            last_line="",
            last_user_message="",
            recent_messages=None,
            recent_structured=None,
            chat_records=None,
            memory_summary="",
            memory_time_hints=None,
            memory_people=None,
            memory_facts=None,
            memory_events=None,
            memory_relations=None,
            environment_text="",
            schema="",
            source="none",
        )

    def _mark_event_done(self, row: "ChatRowState", now: float, *, sent_norm: str = "") -> None:
        bot = self.bot
        mem = bot._baseline.get(row.row_idx)
        if mem is None:
            return
        mem.last_replied_at = now
        if sent_norm:
            mem.last_sent_norm = sent_norm
        mem.pending_unread = False
        mem.pending_normal = False
        mem.pending_mention = False

    def handle_event(
        self,
        *,
        rows: list["ChatRowState"],
        row: "ChatRowState",
        reason: str,
        now: float,
    ) -> None:
        bot = self.bot
        if bot._skip_first_action_pending:
            bot._skip_first_action_pending = False
            mem = bot._baseline.get(row.row_idx)
            if mem is not None:
                if reason == "mention":
                    mem.pending_mention = True
                else:
                    mem.last_replied_at = now
                    mem.pending_unread = False
                    mem.pending_normal = False
                    mem.pending_mention = False
            print(
                f"[skip-startup] row={row.row_idx:>2} | "
                f"reason={reason:<14} | title={bot._fit_col(row.title, 14)}"
            )
            return

        if bot._is_ignored_title(row):
            if bot.cfg.log_verbose or bot.cfg.debug_scan:
                print(f"[skip-title-hard] row={row.row_idx} title={row.title!r}")
            self._mark_event_done(row, now)
            return

        if bot.cfg.log_verbose:
            print(
                f"[event] id={bot._cycle:>4} | row={row.row_idx:>2} | "
                f"reason={reason:<14} | title={bot._fit_col(row.title, 14)}"
            )

        event_is_group = bot._is_group_chat(row)
        event_is_admin = bot._is_admin_session(row)
        event_skip_self_latest = (
            bot.cfg.skip_if_latest_chat_from_self
            and (event_is_group or bot.cfg.skip_if_latest_chat_from_self_private)
        )
        focused_bounds = None
        chat_context = ""
        environment_context = ""
        context_snapshot = self._empty_context_snapshot()
        need_context = (
            bot.llm.is_vision_enabled()
            or (reason == "new_message" and event_skip_self_latest)
            or event_is_admin
            or (
                bot._should_use_llm_decision(event_is_group)
                and bot.cfg.llm_decision.decision_read_chat_context
            )
        )
        focus_candidates = bot._collect_focus_candidates(rows, row, reason)
        if need_context:
            focus_result = bot._focus_chat(
                row,
                focus_candidates=focus_candidates,
                ensure_unread_clear=(reason == "new_message"),
            )
            if (not focus_result.matched) or (focus_result.resolved_row is None):
                seen_w = max(24, bot._term_width() - 17)
                print(
                    f"[skip-focus] row={row.row_idx:>2} | "
                    f"expect={bot._fit_col(row.title, 14)}"
                )
                if focus_result.seen_header:
                    print(f"            seen={bot._fit_col(focus_result.seen_header, seen_w)}")
                return
            resolved = focus_result.resolved_row
            changed_target = (
                resolved.row_idx != row.row_idx
                or bot._title_key(resolved.title) != bot._title_key(row.title)
            )
            if changed_target and bot.cfg.log_verbose:
                print(
                    f"[focus-retarget] from={bot._fit_col(row.title, 14)} "
                    f"-> to={bot._fit_col(resolved.title, 14)}"
                )
            row = resolved
            focused_bounds = focus_result.bounds

        is_group = bot._is_group_chat(row)
        is_admin = bot._is_admin_session(row)
        skip_self_latest = (
            bot.cfg.skip_if_latest_chat_from_self
            and (is_group or bot.cfg.skip_if_latest_chat_from_self_private)
        )
        session_context = bot._build_session_context(row)
        session_history = bot._build_session_history_text(row)
        workspace_context = bot._workspace_context_for_row(
            row,
            is_admin=is_admin,
            skill_query=row.preview or row.text or row.title,
        )
        memory_recall = bot._workspace_memory_recall_for_row(
            row,
            row.preview or row.text,
            is_admin=is_admin,
        )
        if need_context:
            context_snapshot = bot._extract_chat_context(
                focused_bounds,
                title=row.title,
                reason=reason,
                is_group=is_group,
                session_context=session_context,
                session_history=session_history,
                workspace_context=workspace_context,
                memory_recall=memory_recall,
                latest_hint=row.preview or row.text,
                preview=row.preview,
            )
            if context_snapshot.chat_records:
                bot._merge_session_records(
                    row, context_snapshot.chat_records, source=context_snapshot.source
                )
            if context_snapshot.memory_summary:
                bot._apply_session_summary(row, context_snapshot.memory_summary)
            chat_context = context_snapshot.text
            environment_context = context_snapshot.environment_text
            if bot.cfg.debug_scan:
                print(f"[context] row={row.row_idx} text={chat_context!r}")
            if bot.cfg.log_verbose:
                line_w = max(24, bot._term_width() - 12)
                print(
                    f"[ctx] row={row.row_idx:>2} | "
                    f"src={bot._fit_col(context_snapshot.source, 6)} | "
                    f"schema={bot._fit_col(context_snapshot.schema or '-', 14)} | "
                    f"side={bot._fit_col(context_snapshot.last_side, 7)}"
                )
                print(f"      last={bot._fit_col(context_snapshot.last_line, line_w)}")

        if reason == "new_message" and skip_self_latest and context_snapshot.last_side == "self":
            if bot.cfg.log_verbose or bot.cfg.debug_scan:
                print(
                    f"[skip-self-latest] row={row.row_idx} title={row.title!r} "
                    f"preview={row.preview!r} last_line={context_snapshot.last_line!r}"
                )
            self._mark_event_done(row, now)
            bot._save_persistent_memory()
            return

        if is_admin:
            cmd_line = bot._extract_admin_command_text(row, context_snapshot)
            if cmd_line:
                bot._append_session_item(row, "U", cmd_line)
                ack = bot._handle_admin_command(cmd_line)
                if bot.cfg.log_verbose:
                    print(f"[admin-cmd] cmd={cmd_line!r} ack={ack!r}")
                reply_text = bot._reply(
                    row,
                    "admin_command",
                    focused_bounds=focused_bounds,
                    environment_context=environment_context,
                    workspace_context=workspace_context,
                    memory_recall=memory_recall,
                    force_message=ack or "命令已执行。",
                )
                sent_norm = bot._normalize_preview(reply_text)
                self._mark_event_done(row, now, sent_norm=sent_norm)
                bot._remember_sent_for_row(row, sent_norm, now)
                bot._append_session_item(row, "A", reply_text)
                bot._save_persistent_memory()
                return

        latest_user_message = ""
        if context_snapshot.last_user_message:
            latest_user_message = context_snapshot.last_user_message
        elif context_snapshot.last_line and context_snapshot.last_side == "other":
            latest_user_message = context_snapshot.last_line
        else:
            latest_user_message = row.preview or row.text

        memory_recall = bot._workspace_memory_recall_for_row(
            row,
            latest_user_message or row.preview or row.text,
            is_admin=is_admin,
        )

        session_context = bot._build_session_context(row)
        if (not context_snapshot.chat_records) and latest_user_message:
            bot._append_session_record(
                row,
                role="user",
                text=latest_user_message,
                content_type="text",
                sender="",
                source="list",
                count_turn=True,
            )
            session_context = bot._build_session_context(row)
        if not context_snapshot.memory_summary:
            bot._update_long_summary(row)
            session_context = bot._build_session_context(row)

        if bot._is_immediate_reply_event(row, reason):
            should_reply = True
        else:
            should_reply = bot._llm_should_reply_with_context(
                row,
                reason,
                is_group,
                chat_context,
                environment_context,
                session_context,
                workspace_context,
                memory_recall,
            )

        if not should_reply:
            self._mark_event_done(row, now)
            bot._save_persistent_memory()
            return

        bridge_handled, bridge_reply = bot._bridge_reply_text(
            row,
            reason=reason,
            is_group=is_group,
            is_admin=is_admin,
            latest_message=latest_user_message,
            chat_context=chat_context,
            environment_context=environment_context,
            session_context=session_context,
            workspace_context=workspace_context,
            memory_recall=memory_recall,
        )
        if bridge_handled:
            if bridge_reply:
                message = bot._reply(
                    row,
                    reason,
                    focused_bounds=focused_bounds,
                    chat_context=chat_context,
                    environment_context=environment_context,
                    session_context=session_context,
                    workspace_context=workspace_context,
                    memory_recall=memory_recall,
                    latest_message=latest_user_message,
                    force_message=bridge_reply,
                )
                sent_norm = bot._normalize_preview(message)
                self._mark_event_done(row, now, sent_norm=sent_norm)
                bot._remember_sent_for_row(row, sent_norm, now)
                bot._append_session_item(row, "A", message)
                if message and bot._is_normal_reply_event(row, reason):
                    bot._mark_normal_reply_at(now)
            else:
                self._mark_event_done(row, now)
            bot._save_persistent_memory()
            return

        reply_budget = max(1, int(bot.cfg.agent_reply_max_messages_per_turn))
        sent_in_event = 0
        follow_reason = reason

        while sent_in_event < reply_budget:
            planner_hint = ""
            if bot.cfg.agent_actions_enabled:
                tools = bot._available_agent_tools(is_admin=is_admin)
                if tools:
                    memory_recall, planner_hint, _, _, _ = bot._run_agent_planner_loop(
                        planner=bot.llm_planner,
                        row=row,
                        reason=follow_reason,
                        is_group=is_group,
                        is_admin=is_admin,
                        latest_message=latest_user_message,
                        chat_context=chat_context,
                        environment_context=environment_context,
                        session_context=session_context,
                        workspace_context=workspace_context,
                        memory_recall=memory_recall,
                        tools=tools,
                        per_round_max_actions=bot.cfg.agent_actions_max_per_turn,
                    )
                if planner_hint:
                    memory_recall = (
                        f"[planner reply hint]\n{planner_hint}\n\n"
                        + memory_recall
                    )[:3600]
                    if bot.cfg.log_verbose:
                        print(
                            f"[agent] planner hint injected row={row.row_idx:>2} "
                            f"hint={bot._fit_col(planner_hint, max(24, bot._term_width() - 45))}"
                        )
                if sent_in_event > 0:
                    if bot.cfg.log_verbose:
                        print(
                            f"[agent] stop follow-up after first reply "
                            f"row={row.row_idx:>2} sent={sent_in_event}"
                        )
                    break

            message = bot._reply(
                row,
                reason,
                focused_bounds=focused_bounds,
                chat_context=chat_context,
                environment_context=environment_context,
                session_context=session_context,
                workspace_context=workspace_context,
                memory_recall=memory_recall,
                latest_message=latest_user_message,
                force_message="",
            )
            if not (message or "").strip():
                if bot.cfg.log_verbose:
                    print(f"[agent] stop empty reply row={row.row_idx:>2} sent={sent_in_event}")
                break

            sent_norm = bot._normalize_preview(message)
            had_memory = bot._baseline.get(row.row_idx) is not None
            self._mark_event_done(row, now, sent_norm=sent_norm)
            if had_memory:
                bot._remember_sent_for_row(row, sent_norm, now)
            if had_memory and message and bot._is_normal_reply_event(row, reason):
                bot._mark_normal_reply_at(now)
            bot._append_session_item(row, "A", message)
            bot._save_persistent_memory()
            sent_in_event += 1
            if not bot.cfg.agent_actions_enabled:
                break
            if sent_in_event >= reply_budget:
                if bot.cfg.log_verbose:
                    print(
                        f"[agent] reply budget reached row={row.row_idx:>2} "
                        f"budget={reply_budget}"
                    )
                break

            chat_context = bot._build_session_history_text(row)
            session_context = bot._build_session_context(row)
            follow_reason = "planner_follow_up"
            if bot.cfg.log_verbose:
                print(
                    f"[agent] multi-send continue row={row.row_idx:>2} "
                    f"next={sent_in_event + 1}/{reply_budget}"
                )

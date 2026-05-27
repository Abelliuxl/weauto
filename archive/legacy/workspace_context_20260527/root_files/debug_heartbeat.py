#!/usr/bin/env python3
from __future__ import annotations

import argparse
import builtins
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

_BUILTIN_PRINT = builtins.print
_COLOR_ENABLED = bool(
    os.getenv("FORCE_COLOR", "").strip()
    or (
        sys.stdout.isatty()
        and (not os.getenv("NO_COLOR", "").strip())
        and os.getenv("TERM", "").lower() != "dumb"
    )
)
_COLOR_RESET = "\033[0m"
_LOG_COLOR_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\[err\]"), "\033[91m"),
    (re.compile(r"^\[warn\]"), "\033[33m"),
    (re.compile(r"^\[summary\]"), "\033[96m"),
    (re.compile(r"^\[ok\]"), "\033[92m"),
    (re.compile(r"^\[start\]"), "\033[96m"),
    (re.compile(r"^\[done\]"), "\033[96m"),
]
_PROBE_RECORDS: list[dict[str, object]] = []


def _colorize_log_line(text: str) -> str:
    if (not _COLOR_ENABLED) or (not text):
        return text
    clean = str(text)
    for pattern, color in _LOG_COLOR_RULES:
        if pattern.match(clean):
            return f"{color}{clean}{_COLOR_RESET}"
    return clean


def print(*args, **kwargs):  # type: ignore[override]
    if not args:
        return _BUILTIN_PRINT(*args, **kwargs)
    sep = kwargs.get("sep", " ")
    merged = sep.join(str(x) for x in args)
    file_obj = kwargs.get("file", sys.stdout)
    if file_obj in (None, sys.stdout, sys.stderr):
        merged = _colorize_log_line(merged)
    out_kwargs = dict(kwargs)
    out_kwargs["sep"] = ""
    return _BUILTIN_PRINT(merged, **out_kwargs)


def _maybe_reexec_with_project_venv() -> None:
    if sys.version_info < (3, 13):
        return

    root_dir = Path(__file__).resolve().parent
    venv_python = root_dir / ".venv312" / "bin" / "python"
    if not venv_python.exists():
        return

    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_maybe_reexec_with_project_venv()

from PIL import Image

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Heartbeat debug probe. Default runs real heartbeat execution. "
            "Use --safe for probe-only mode (no write actions)."
        )
    )
    parser.add_argument("--config", default="config.toml", help="Path to TOML config file")
    parser.add_argument("--days", type=int, default=7, help="Days window for memory digest probe")
    parser.add_argument("--runs", type=int, default=1, help="How many heartbeat runs in execute mode")
    parser.add_argument("--sleep-sec", type=float, default=0.5, help="Sleep between execute runs")
    parser.add_argument("--show-tasks", action="store_true", help="Print HEARTBEAT.md actionable lines")
    parser.add_argument("--force-enable", action="store_true", help="Force heartbeat_enabled=true in this probe")
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Probe-only mode: do not execute maintain/refine/_run_heartbeat",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly run real maintain/refine/_run_heartbeat (default behavior)",
    )
    parser.add_argument(
        "--probe-summary",
        action="store_true",
        help="Also probe llm.summarize_session (to debug summary timeout warnings)",
    )
    parser.add_argument(
        "--probe-reply",
        action="store_true",
        help="Also probe llm.generate (to debug reply backend timeout warnings)",
    )
    parser.add_argument(
        "--probe-vision",
        action="store_true",
        help="Also probe vision analyze_chat_image with a synthetic image",
    )
    parser.add_argument(
        "--no-traceback",
        action="store_true",
        help="Do not print traceback on probe failure",
    )
    return parser.parse_args()


def _preview_text(text: str, limit: int = 220) -> str:
    raw = str(text or "").replace("\n", "\\n")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "...(truncated)"


def _run_probe(
    name: str,
    fn,
    *args,
    show_traceback: bool,
    **kwargs,
):
    started = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.monotonic() - started
        _PROBE_RECORDS.append(
            {
                "name": str(name),
                "ok": True,
                "elapsed": float(elapsed),
                "error": "",
            }
        )
        print(f"[ok] {name:<36} elapsed={elapsed:.2f}s")
        return True, result, elapsed
    except Exception as exc:
        elapsed = time.monotonic() - started
        _PROBE_RECORDS.append(
            {
                "name": str(name),
                "ok": False,
                "elapsed": float(elapsed),
                "error": str(exc),
            }
        )
        print(f"[err] {name:<36} elapsed={elapsed:.2f}s type={type(exc).__name__} err={exc}")
        if show_traceback:
            traceback.print_exc()
        return False, None, elapsed


def _dump_json(label: str, obj) -> None:
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        text = repr(obj)
    print(f"[data] {label}:\n{text}")


def _print_llm_cfg(label: str, llm_cfg) -> None:
    print(
        f"[cfg] {label} "
        f"enabled={llm_cfg.enabled} model={llm_cfg.model} timeout={llm_cfg.timeout_sec:.1f}s "
        f"base_url={llm_cfg.base_url} "
        f"ollama_think={llm_cfg.ollama_think!r} compat_think={llm_cfg.openai_compat_think_mode!r}"
    )


def _print_summary(*, execute_mode: bool) -> None:
    total = len(_PROBE_RECORDS)
    ok_count = sum(1 for x in _PROBE_RECORDS if bool(x.get("ok")))
    err_count = max(0, total - ok_count)
    total_elapsed = sum(float(x.get("elapsed", 0.0) or 0.0) for x in _PROBE_RECORDS)
    print(
        f"[summary] probes total={total} ok={ok_count} err={err_count} "
        f"elapsed={total_elapsed:.2f}s mode={'execute' if execute_mode else 'safe'}"
    )
    if total > 0:
        slowest = sorted(
            _PROBE_RECORDS,
            key=lambda x: float(x.get("elapsed", 0.0) or 0.0),
            reverse=True,
        )[:5]
        for item in slowest:
            name = str(item.get("name", "-"))
            elapsed = float(item.get("elapsed", 0.0) or 0.0)
            status = "ok" if bool(item.get("ok")) else "err"
            print(f"[summary] slow {status:<3} {name:<36} {elapsed:>5.2f}s")
    if err_count > 0:
        failures = [x for x in _PROBE_RECORDS if not bool(x.get("ok"))][:8]
        for item in failures:
            name = str(item.get("name", "-"))
            err = _preview_text(str(item.get("error", "")), limit=240)
            print(f"[summary] fail {name}: {err}")


def main() -> None:
    args = parse_args()
    show_traceback = not args.no_traceback
    days = max(1, min(14, int(args.days)))
    execute_mode = (not args.safe) or args.execute
    _PROBE_RECORDS.clear()

    cfg = load_config(args.config)
    if args.force_enable:
        cfg.heartbeat_enabled = True

    print("[start] heartbeat debug probe")
    print(f"[start] config={args.config}")
    print(
        "[cfg] heartbeat "
        f"enabled={cfg.heartbeat_enabled} interval={cfg.heartbeat_interval_sec:.1f}s "
        f"idle_min={cfg.heartbeat_min_idle_sec:.1f}s max_actions={cfg.heartbeat_max_actions} "
        f"fail_open={cfg.heartbeat_fail_open}"
    )
    _print_llm_cfg("llm_reply", cfg.llm_reply)
    _print_llm_cfg("llm_summary", cfg.llm_summary)
    _print_llm_cfg("llm_heartbeat", cfg.llm_heartbeat)
    print(
        "[cfg] vision "
        f"enabled={cfg.vision.enabled} model={cfg.vision.model} timeout={cfg.vision.timeout_sec:.1f}s "
        f"base_url={cfg.vision.base_url} "
        f"ollama_think={cfg.vision.ollama_think!r} compat_think={cfg.vision.openai_compat_think_mode!r} "
        f"response_format_json_object={cfg.vision.response_format_json_object}"
    )
    print(
        "[cfg] skip-self-latest "
        f"enabled={cfg.skip_if_latest_chat_from_self} "
        f"private={cfg.skip_if_latest_chat_from_self_private}"
    )
    print(
        "[cfg] note "
        "longcat heartbeat empty-body issues are usually provider compatibility "
        "with reasoning controls; this probe runs the real heartbeat chain."
    )
    print(f"[mode] execute_mode={execute_mode} (set --safe for probe-only)")

    ok, bot_obj, _ = _run_probe("bot init", WeChatGuiRpaBot, cfg, show_traceback=show_traceback)
    if not ok or bot_obj is None:
        print("[stop] bot init failed")
        return
    bot: WeChatGuiRpaBot = bot_obj

    _, tasks_text, _ = _run_probe(
        "heartbeat._load_heartbeat_tasks",
        bot._load_heartbeat_tasks,
        show_traceback=show_traceback,
    )
    tasks_text = str(tasks_text or "")
    print(f"[data] heartbeat tasks chars={len(tasks_text)} lines={len(tasks_text.splitlines())}")
    if args.show_tasks:
        print("[data] heartbeat tasks text:")
        print(tasks_text or "(empty)")

    _, direct_actions, _ = _run_probe(
        "heartbeat._parse_direct_actions",
        bot._parse_heartbeat_direct_actions,
        tasks_text,
        show_traceback=show_traceback,
    )
    _dump_json("direct_actions", direct_actions or [])

    _, row_obj, _ = _run_probe(
        "heartbeat._heartbeat_virtual_row",
        bot._heartbeat_virtual_row,
        show_traceback=show_traceback,
    )
    if row_obj is None:
        print("[stop] virtual row unavailable")
        return
    row = row_obj
    is_admin = bool(cfg.admin_commands_enabled)

    _, session_context, _ = _run_probe(
        "heartbeat._build_session_context",
        bot._build_session_context,
        row,
        show_traceback=show_traceback,
    )
    _, chat_context, _ = _run_probe(
        "heartbeat._build_session_history_text",
        bot._build_session_history_text,
        row,
        show_traceback=show_traceback,
    )
    _, workspace_context, _ = _run_probe(
        "heartbeat._workspace_context_for_row",
        bot._workspace_context_for_row,
        row,
        is_admin=is_admin,
        show_traceback=show_traceback,
    )
    _, memory_recall, _ = _run_probe(
        "heartbeat._workspace_memory_recall",
        bot._workspace_memory_recall_for_row,
        row,
        tasks_text or "heartbeat",
        is_admin=is_admin,
        show_traceback=show_traceback,
    )
    _, tools, _ = _run_probe(
        "heartbeat._available_heartbeat_tools",
        bot._available_heartbeat_tools,
        show_traceback=show_traceback,
    )
    _dump_json("heartbeat_tools", tools or [])
    _, backend_pairs, _ = _run_probe(
        "heartbeat._heartbeat_llm_backends",
        bot._heartbeat_llm_backends,
        show_traceback=show_traceback,
    )
    _dump_json(
        "heartbeat_llm_backends",
        [str(name) for name, _ in (backend_pairs or [])],
    )

    now_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    environment_context = (
        f"[heartbeat_prompt]\n{cfg.heartbeat_prompt}\n\n"
        f"[heartbeat_tasks]\n{tasks_text}\n\n"
        f"[current_time]\n{now_text}"
    )[:2200]

    _, plan, _ = _run_probe(
        "llm_heartbeat.plan_actions(heartbeat)",
        bot.llm_heartbeat.plan_actions,
        title=row.title,
        is_group=False,
        reason="heartbeat",
        latest_message=(tasks_text.split("\n", 1)[0] if tasks_text else "heartbeat"),
        chat_context=str(chat_context or ""),
        environment_context=environment_context,
        session_context=str(session_context or ""),
        workspace_context=str(workspace_context or ""),
        memory_recall=str(memory_recall or ""),
        available_tools=list(tools or []),
        max_actions=max(1, int(cfg.heartbeat_max_actions)),
        show_traceback=show_traceback,
    )
    _dump_json("planner_output", plan or {})

    recent = bot._collect_recent_daily_memory(days=days, max_chars=2400)
    memory_path = Path(cfg.workspace_dir) / "MEMORY.md"
    existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    print(
        f"[data] memory source existing_chars={len(existing)} recent_daily_chars={len(recent)} days={days}"
    )

    _, digest, _ = _run_probe(
        "llm_heartbeat.heartbeat_memory_digest",
        bot.llm_heartbeat.heartbeat_memory_digest,
        existing_memory=existing[:5000],
        recent_daily_memory=recent,
        max_items=12,
        show_traceback=show_traceback,
    )
    print(f"[data] digest_preview={_preview_text(str(digest or ''))}")

    workspace = Path(cfg.workspace_dir)
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8") if (workspace / "SOUL.md").exists() else ""
    identity = (
        (workspace / "IDENTITY.md").read_text(encoding="utf-8")
        if (workspace / "IDENTITY.md").exists()
        else ""
    )
    user = (workspace / "USER.md").read_text(encoding="utf-8") if (workspace / "USER.md").exists() else ""
    tools_text = (workspace / "TOOLS.md").read_text(encoding="utf-8") if (workspace / "TOOLS.md").exists() else ""
    _, refined_docs, _ = _run_probe(
        "llm_heartbeat.heartbeat_refine_persona_docs",
        bot.llm_heartbeat.heartbeat_refine_persona_docs,
        soul=soul[:4000],
        identity=identity[:4000],
        user=user[:4000],
        tools=tools_text[:4000],
        memory=existing[:5000],
        show_traceback=show_traceback,
    )
    _dump_json(
        "refined_docs_meta",
        {
            "keys": sorted(list((refined_docs or {}).keys())),
            "sizes": {k: len(str(v)) for k, v in (refined_docs or {}).items()},
        },
    )

    if args.probe_summary:
        _, summary_out, _ = _run_probe(
            "llm_summary.summarize_session",
            bot.llm_summary.summarize_session,
            title=row.title,
            previous_summary="",
            short_items=["U:这是一个summary超时诊断探针", "A:收到"],
            show_traceback=show_traceback,
        )
        print(f"[data] summary_preview={_preview_text(str(summary_out or ''))}")

    if args.probe_reply:
        _, reply_out, _ = _run_probe(
            "llm_reply.generate(reply_probe)",
            bot.llm_reply.generate,
            title=row.title,
            preview="测试：请回复一句用于超时诊断",
            reason="debug_probe",
            latest_message="请回复一句用于超时诊断",
            chat_context="U:请回复一句用于超时诊断",
            environment_context="",
            session_context="",
            workspace_context="",
            memory_recall="",
            avoid_replies=[],
            show_traceback=show_traceback,
        )
        print(f"[data] reply_preview={_preview_text(str(reply_out or ''))}")

    if args.probe_vision:
        probe_img = Image.new("RGB", (1080, 720), (248, 248, 248))
        _, vision_out, _ = _run_probe(
            "vision.analyze_chat_image(probe)",
            bot.llm_reply.analyze_chat_image,
            image=probe_img,
            title=row.title,
            reason="debug_probe",
            is_group=False,
            session_context="",
            session_history="",
            latest_hint="测试视觉探针",
            preview="测试视觉探针",
            workspace_context="",
            memory_recall="",
            avoid_replies=[],
            show_traceback=show_traceback,
        )
        if isinstance(vision_out, dict):
            _dump_json(
                "vision_probe_meta",
                {
                    "schema": vision_out.get("schema"),
                    "confidence": vision_out.get("confidence"),
                    "context_keys": sorted(
                        list((vision_out.get("context") or {}).keys())
                        if isinstance(vision_out.get("context"), dict)
                        else []
                    ),
                    "env_keys": sorted(
                        list((vision_out.get("environment") or {}).keys())
                        if isinstance(vision_out.get("environment"), dict)
                        else []
                    ),
                },
            )

    if execute_mode:
        print("[exec] execute mode enabled; maintenance methods may update workspace files")
        _run_probe(
            f"heartbeat._heartbeat_maintain_memory(days={days})",
            bot._heartbeat_maintain_memory,
            days=days,
            show_traceback=show_traceback,
        )
        _run_probe(
            "heartbeat._heartbeat_refine_persona_files",
            bot._heartbeat_refine_persona_files,
            show_traceback=show_traceback,
        )

        for i in range(1, max(1, int(args.runs)) + 1):
            print(f"[exec] heartbeat run {i}/{max(1, int(args.runs))}")
            _run_probe(
                "heartbeat._run_heartbeat",
                bot._run_heartbeat,
                time.time(),
                [],
                show_traceback=show_traceback,
            )
            if i < max(1, int(args.runs)):
                time.sleep(max(0.0, float(args.sleep_sec)))
    else:
        print("[exec] safe mode: skipped maintain/refine/_run_heartbeat")

    _print_summary(execute_mode=execute_mode)
    print("[done] heartbeat debug probe finished")


if __name__ == "__main__":
    main()

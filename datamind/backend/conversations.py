"""
conversations.py
================
All database operations for the conversation memory system.

Conversations are persistent chat threads stored in the DataMind internal DB.
Each thread holds a sequence of user/assistant message pairs. The history is
injected into the LLM prompt on every request so the model understands
follow-up questions without any keyword detection.
"""

import json
import os
import threading
from typing import Optional

from pool import get_internal_conn as _get_conn
from logger import get_logger

log = get_logger(__name__)

# How many recent messages to include in the LLM context prompt (full history).
_HISTORY_WINDOW     = int(os.getenv("CONV_HISTORY_WINDOW", "20"))
# After this many messages, include a summary + only the last N messages.
_SUMMARY_THRESHOLD  = int(os.getenv("CONV_SUMMARY_THRESHOLD", "20"))
# How many recent messages to keep after summarisation kicks in.
_POST_SUMMARY_TAIL  = int(os.getenv("CONV_POST_SUMMARY_TAIL", "5"))
# Max rows stored in data_snapshot per message.
_SNAPSHOT_ROWS      = 10


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_snapshot(columns: list, data: list, stat_col: str = None, analysis: str = None) -> dict:
    """
    Build a compact data snapshot for storing with an assistant message.
    Stores column names + first SNAPSHOT_ROWS rows + one stat line.
    This is what gets sent back to the LLM as context — NOT the full result.
    """
    rows = data[:_SNAPSHOT_ROWS]
    stat = None
    if stat_col and data:
        try:
            total = sum(float(r.get(stat_col, 0) or 0) for r in data)
            stat = f"{stat_col}={total:,.2f} (total of {len(data)} rows)"
        except Exception:
            pass
    snapshot = {"columns": columns, "rows": rows, "stat": stat}
    if analysis:
        snapshot["analysis"] = analysis
    return snapshot


def _fmt_snapshot(snapshot: dict) -> str:
    """Format a data_snapshot dict as a compact string for the LLM prompt."""
    if not snapshot:
        return ""
    cols = snapshot.get("columns", [])
    rows = snapshot.get("rows", [])
    stat = snapshot.get("stat")
    parts = [f"columns={cols}"]
    if rows:
        sample = [list(r.values()) if isinstance(r, dict) else r for r in rows[:3]]
        parts.append(f"sample={sample}")
    if stat:
        parts.append(f"stat={stat}")
    return "[Data: " + ", ".join(parts) + "]"


# ── Write operations ──────────────────────────────────────────────────────────

def create_conversation(user_email: str, conv_id: str) -> dict:
    """Create a new conversation row. Returns the created row."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO conversations (id, user_email, title, message_count)
            VALUES (%s, %s, 'New conversation', 0)
            """,
            (conv_id, user_email),
        )
        conn.commit()
        log.debug("Conversation created", conv_id=conv_id, user=user_email)
        _sync_connected_providers(user_email)
        return {"id": conv_id, "user_email": user_email,
                "title": "New conversation", "message_count": 0}
    finally:
        conn.close()


def _sync_connected_providers(user_email: str) -> None:
    """Kick off a delta sync for each healthy connected integration so a new
    chat session always sees the latest data. Non-fatal — _start_sync_thread
    dedupes against syncs already in flight, so this is safe to call on every
    new conversation."""
    try:
        from integrations import list_integrations, trigger_sync
        for integ in list_integrations(user_email):
            if integ.get("status") in ("active", "syncing"):
                trigger_sync(user_email, integ["provider_id"], full=False)
    except Exception as e:
        log.warning("New-chat sync trigger failed (non-fatal)", user=user_email, error=str(e))


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    sql_query: str = None,
    row_count: int = 0,
    columns: list = None,
    data: list = None,
    stat_col: str = None,
    analysis: str = None,
) -> int:
    """
    Insert one message and increment the parent conversation's counter.
    Returns the new message id.

    data_snapshot is built automatically from columns + data (capped at
    SNAPSHOT_ROWS rows). Pass None for user messages (no data to snapshot).
    """
    snapshot = None
    if role == "assistant" and columns and data is not None:
        snapshot = _make_snapshot(columns, data, stat_col, analysis)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO conversation_messages
                (conversation_id, role, content, sql_query, row_count, data_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                conversation_id, role, content, sql_query, row_count,
                json.dumps(snapshot) if snapshot else None,
            ),
        )
        msg_id = cur.lastrowid
        cur.execute(
            """
            UPDATE conversations
            SET message_count = message_count + 1,
                updated_at    = NOW()
            WHERE id = %s
            """,
            (conversation_id,),
        )
        conn.commit()
        return msg_id
    finally:
        conn.close()


def update_title(conv_id: str, title: str) -> None:
    """Set the LLM-generated title on a conversation."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE conversations SET title = %s WHERE id = %s",
            (title[:255], conv_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_summary(conv_id: str, summary_text: str, covers_up_to_id: int) -> None:
    """Persist a compression summary and copy it to conversations.summary."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO conversation_summaries
                (conversation_id, summary_text, covers_up_to_id)
            VALUES (%s, %s, %s)
            """,
            (conv_id, summary_text, covers_up_to_id),
        )
        cur.execute(
            "UPDATE conversations SET summary = %s WHERE id = %s",
            (summary_text, conv_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Read operations ───────────────────────────────────────────────────────────

def get_conversation(conv_id: str, user_email: str) -> Optional[dict]:
    """Fetch one conversation, enforcing ownership."""
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM conversations WHERE id = %s AND user_email = %s",
            (conv_id, user_email),
        )
        return cur.fetchone()
    finally:
        conn.close()


def list_conversations(user_email: str, limit: int = 50) -> list:
    """Return the most recent conversations for a user (newest first)."""
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, title, message_count, created_at, updated_at
            FROM conversations
            WHERE user_email = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user_email, limit),
        )
        rows = cur.fetchall()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
            if r.get("updated_at"):
                r["updated_at"] = str(r["updated_at"])
        return rows
    finally:
        conn.close()


def get_messages(conv_id: str, user_email: str) -> list:
    """
    Return all messages in a conversation ordered oldest-first.
    Enforces ownership via a JOIN on conversations.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT cm.id, cm.role, cm.content, cm.row_count,
                   cm.data_snapshot, cm.created_at
            FROM conversation_messages cm
            JOIN conversations c ON c.id = cm.conversation_id
            WHERE cm.conversation_id = %s AND c.user_email = %s
            ORDER BY cm.created_at ASC, cm.id ASC
            """,
            (conv_id, user_email),
        )
        rows = cur.fetchall()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
            if r.get("data_snapshot") and isinstance(r["data_snapshot"], str):
                try:
                    r["data_snapshot"] = json.loads(r["data_snapshot"])
                except Exception:
                    pass
        return rows
    finally:
        conn.close()


def delete_conversation(conv_id: str, user_email: str) -> bool:
    """
    Delete a conversation and all its messages (CASCADE handles messages).
    Returns True if a row was deleted, False if not found or not owner.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM conversations WHERE id = %s AND user_email = %s",
            (conv_id, user_email),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── History builder ───────────────────────────────────────────────────────────

def get_history_for_prompt(conv_id: str) -> str:
    """
    Load conversation history and format it as a string ready to be injected
    into the LLM system prompt.

    Strategy:
    - If the conversation has a stored summary AND more than POST_SUMMARY_TAIL
      messages exist, return:  summary + last POST_SUMMARY_TAIL messages.
    - Otherwise return the last HISTORY_WINDOW messages.

    This keeps the prompt size bounded even in very long conversations.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)

        # Check for an existing summary
        cur.execute(
            """
            SELECT summary_text, covers_up_to_id
            FROM conversation_summaries
            WHERE conversation_id = %s
            ORDER BY covers_up_to_id DESC
            LIMIT 1
            """,
            (conv_id,),
        )
        summary_row = cur.fetchone()

        if summary_row:
            # Load only the messages AFTER the summary cutoff
            cur.execute(
                """
                SELECT role, content, data_snapshot
                FROM conversation_messages
                WHERE conversation_id = %s AND id > %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (conv_id, summary_row["covers_up_to_id"], _POST_SUMMARY_TAIL * 2),
            )
            recent = cur.fetchall()
            return _build_prompt(summary_row["summary_text"], recent)
        else:
            # No summary yet — load up to HISTORY_WINDOW messages
            cur.execute(
                """
                SELECT role, content, data_snapshot
                FROM conversation_messages
                WHERE conversation_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (conv_id, _HISTORY_WINDOW),
            )
            # Fetched newest-first, reverse to chronological
            msgs = list(reversed(cur.fetchall()))
            return _build_prompt(None, msgs)
    finally:
        conn.close()


def _build_prompt(summary: Optional[str], messages: list) -> str:
    """
    Format history into a compact multi-turn block for the LLM system prompt.

    Output example:
        Earlier in this conversation: User asked about total revenue last month
        ($44M) and top customers by spend. The shop 'Negros Route' dominated.

        User: What was my total revenue last month?
        Assistant: Found 1 result. total_revenue_last_month = 44,991,596.55
        [Data: columns=['total_revenue_last_month'], sample=[[44991596.55]], stat=...]

        User: Who are my top 10 customers?
        Assistant: Found 10 results. Top customer: Alice Johnson = 128,340.00
        [Data: columns=['customer_name','total_spent'], sample=[...]]
    """
    parts = []

    if summary:
        parts.append(f"Earlier in this conversation: {summary}\n")

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        snapshot = msg.get("data_snapshot")

        # Parse snapshot from JSON string if needed
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = None

        if role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
            if snapshot:
                parts.append(_fmt_snapshot(snapshot))

    return "\n".join(parts)


# ── Background jobs ───────────────────────────────────────────────────────────

def trigger_title_generation(
    conv_id: str, first_question: str, first_answer: str,
    llm: str, api_key: str, user_email: str,
) -> None:
    """
    Fire-and-forget: generate a short title from the first exchange and save it.
    Runs in a daemon thread — does not block the API response.
    """
    def _run():
        try:
            from llm import call_llm
            prompt = (
                f"Generate a short title (max 7 words) for a data analysis "
                f"conversation that started with:\n"
                f"Question: {first_question[:200]}\n"
                f"Answer: {first_answer[:200]}\n"
                f"Reply with the title only. No quotes. No punctuation at the end."
            )
            title = call_llm(
                prompt,
                system="You generate concise titles. Reply with the title only.",
                llm=llm,
                max_tokens=25,
                api_key=api_key,
                user_email=None,  # don't charge for title generation
            )
            title = title.strip().strip('"').strip("'")[:255]
            if title:
                update_title(conv_id, title)
                log.debug("Conversation title generated", conv_id=conv_id, title=title)
        except Exception as e:
            log.warning("Title generation failed", conv_id=conv_id, error=str(e))

    threading.Thread(target=_run, daemon=True).start()


def trigger_summarisation(
    conv_id: str, llm: str, api_key: str, user_email: str,
) -> None:
    """
    Fire-and-forget: summarise the older portion of a long conversation.
    Triggered when message_count reaches SUMMARY_THRESHOLD and then every
    5 messages afterward.
    Runs in a daemon thread — does not block the API response.
    """
    def _run():
        try:
            conn = _get_conn()
            try:
                cur = conn.cursor(dictionary=True)
                # Find the cutoff: everything except the last POST_SUMMARY_TAIL pairs
                tail_count = _POST_SUMMARY_TAIL * 2  # user + assistant pairs
                cur.execute(
                    """
                    SELECT id, role, content
                    FROM conversation_messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (conv_id,),
                )
                all_msgs = cur.fetchall()
            finally:
                conn.close()

            if len(all_msgs) <= tail_count:
                return  # not enough messages to summarise

            to_summarise = all_msgs[:-tail_count]
            cutoff_id = to_summarise[-1]["id"]

            transcript = "\n".join(
                f"{m['role'].capitalize()}: {m['content'][:300]}"
                for m in to_summarise
            )

            from llm import call_llm
            prompt = (
                f"Summarise this data analysis conversation in 2-3 sentences. "
                f"Focus on what was analysed, key findings, and any patterns "
                f"the user was interested in. Be specific about numbers.\n\n"
                f"{transcript}"
            )
            summary = call_llm(
                prompt,
                system="You write concise conversation summaries.",
                llm=llm,
                max_tokens=150,
                api_key=api_key,
                user_email=None,  # don't charge for summarisation
            )
            summary = summary.strip()
            if summary:
                save_summary(conv_id, summary, cutoff_id)
                log.debug("Conversation summarised", conv_id=conv_id,
                          cutoff_id=cutoff_id)
        except Exception as e:
            log.warning("Summarisation failed", conv_id=conv_id, error=str(e))

    threading.Thread(target=_run, daemon=True).start()

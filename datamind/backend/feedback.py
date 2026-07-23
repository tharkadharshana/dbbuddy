"""
feedback.py
===========
Widget feedback: 5-star rating + optional comment, collected when the
embed widget is closed. Stored so partners/admins can review user sentiment.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from logger import get_logger
from auth import current_user
from pool import get_internal_conn as _get_conn

log = get_logger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])


def bootstrap_feedback_tables():
    """Create widget_feedback table. Safe to call on every startup."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS widget_feedback (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_email   VARCHAR(255) NOT NULL,
            rating       TINYINT      NOT NULL,
            comment      TEXT,
            created_at   DATETIME     DEFAULT NOW(),
            INDEX (user_email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    cursor.close()
    conn.close()
    log.info("Feedback table bootstrapped")


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2000)


@router.post("/feedback")
def submit_feedback(req: FeedbackRequest, user: dict = Depends(current_user)):
    """Store a widget rating/comment against the authenticated embed user."""
    conn = _get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO widget_feedback (user_email, rating, comment) VALUES (%s, %s, %s)",
            (user["email"], req.rating, (req.comment or "").strip() or None),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/feedback/status")
def get_feedback_status(user: dict = Depends(current_user)):
    """
    Whether this user has ever submitted widget feedback — checked server-side
    (not localStorage) so the prompt doesn't reappear on a different browser/device.
    """
    conn = _get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM widget_feedback WHERE user_email = %s LIMIT 1",
            (user["email"],),
        )
        has_feedback = cursor.fetchone() is not None
        cursor.close()
    finally:
        conn.close()
    return {"has_feedback": has_feedback}

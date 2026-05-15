"""
credits.py
==========
Credits and usage tracking system.
Tracks AI token usage and database row processing.
Deducts credits from user balance based on LLM usage.
"""
import os
from typing import Optional, Dict, List
from datetime import datetime
import mysql.connector
from logger import get_logger

log = get_logger(__name__)


def _get_conn():
    """Connect to DataMind's internal database."""
    return mysql.connector.connect(
        host=os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port=int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database=os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user=os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password=os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


def bootstrap_credit_tables():
    """
    Create credit tracking tables in DataMind's internal database.
    Safe to run on every startup (IF NOT EXISTS).
    """
    conn = _get_conn()
    cursor = conn.cursor()
    
    # User credits table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_credits (
            user_email VARCHAR(255) PRIMARY KEY,
            ai_credits DECIMAL(10, 2) DEFAULT 100.00,
            total_tokens_used BIGINT DEFAULT 0,
            total_db_rows INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    
    # Credit usage log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_usage_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_email VARCHAR(255) NOT NULL,
            usage_type ENUM('ai_tokens', 'db_rows', 'integration_sync') NOT NULL,
            amount DECIMAL(10, 2) NOT NULL,
            tokens_used INT DEFAULT 0,
            model VARCHAR(50),
            endpoint VARCHAR(255),
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_user_email (user_email),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    
    # Pricing configuration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pricing_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            config_key VARCHAR(100) UNIQUE NOT NULL,
            config_value DECIMAL(10, 6) NOT NULL,
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    
    # Insert default pricing (only if table is empty)
    cursor.execute("SELECT COUNT(*) FROM pricing_config")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO pricing_config (config_key, config_value, description) VALUES
            ('price_per_1k_tokens_gpt4', 0.03, 'Price per 1000 tokens for GPT-4'),
            ('price_per_1k_tokens_claude', 0.015, 'Price per 1000 tokens for Claude Sonnet'),
            ('price_per_1k_tokens_gemini', 0.001, 'Price per 1000 tokens for Gemini Pro'),
            ('price_per_1k_tokens_deepseek', 0.0003, 'Price per 1000 tokens for DeepSeek'),
            ('price_per_1k_db_rows', 0.001, 'Price per 1000 database rows synced'),
            ('monthly_credit_limit', 100.00, 'Default monthly credit limit for new users')
        """)
    
    conn.commit()
    conn.close()
    log.info("Credit tracking tables bootstrapped")


def get_user_credits(user_email: str) -> Dict:
    """Get user's current credit balance and usage."""
    conn = _get_conn()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get or create credit record
        cursor.execute("""
            INSERT INTO user_credits (user_email, ai_credits)
            VALUES (%s, (SELECT config_value FROM pricing_config WHERE config_key = 'monthly_credit_limit'))
            ON DUPLICATE KEY UPDATE user_email=user_email
        """, (user_email,))
        conn.commit()
        
        cursor.execute("""
            SELECT ai_credits, total_tokens_used, total_db_rows, updated_at
            FROM user_credits
            WHERE user_email = %s
        """, (user_email,))
        
        row = cursor.fetchone()
        if not row:
            return {
                "credits_remaining": 100.00,
                "total_tokens_used": 0,
                "total_db_rows": 0,
                "last_updated": None
            }
        
        return {
            "credits_remaining": float(row["ai_credits"]),
            "total_tokens_used": row["total_tokens_used"],
            "total_db_rows": row["total_db_rows"],
            "last_updated": row["updated_at"].isoformat() if row["updated_at"] else None
        }
    finally:
        conn.close()


def deduct_credits(
    user_email: str,
    amount: float,
    usage_type: str,
    tokens_used: int = 0,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
    description: Optional[str] = None
):
    """Deduct credits from user's balance and log usage."""
    conn = _get_conn()
    cursor = conn.cursor()
    
    try:
        # Ensure a credit record exists, then deduct atomically
        cursor.execute("""
            INSERT INTO user_credits (user_email, ai_credits, total_tokens_used)
            VALUES (%s, (SELECT config_value FROM pricing_config WHERE config_key = 'monthly_credit_limit'), %s)
            ON DUPLICATE KEY UPDATE
                ai_credits = GREATEST(ai_credits - %s, 0),
                total_tokens_used = total_tokens_used + %s,
                updated_at = NOW()
        """, (user_email, tokens_used, amount, tokens_used))
        
        # Log usage
        cursor.execute("""
            INSERT INTO credit_usage_log
            (user_email, usage_type, amount, tokens_used, model, endpoint, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (user_email, usage_type, amount, tokens_used, model, endpoint, description))
        
        conn.commit()
        
        log.info("Credits deducted",
                 user=user_email,
                 amount=amount,
                 type=usage_type,
                 tokens=tokens_used)
    finally:
        conn.close()


def calculate_token_cost(tokens: int, model: str) -> float:
    """Calculate cost for token usage based on model."""
    conn = _get_conn()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Map model names to pricing keys
        pricing_map = {
            "gpt-4": "price_per_1k_tokens_gpt4",
            "gpt-4-turbo": "price_per_1k_tokens_gpt4",
            "claude-3-sonnet": "price_per_1k_tokens_claude",
            "claude-3-opus": "price_per_1k_tokens_claude",
            "gemini-pro": "price_per_1k_tokens_gemini",
            "gemini-1.5-flash": "price_per_1k_tokens_gemini",
            "gemini-2.0-flash": "price_per_1k_tokens_gemini",
            "gemini": "price_per_1k_tokens_gemini",
            "deepseek-chat": "price_per_1k_tokens_deepseek",
            "deepseek": "price_per_1k_tokens_deepseek",
        }
        
        pricing_key = pricing_map.get(model.lower(), "price_per_1k_tokens_gemini")
        
        cursor.execute("""
            SELECT config_value FROM pricing_config WHERE config_key = %s
        """, (pricing_key,))
        
        row = cursor.fetchone()
        price_per_1k = float(row["config_value"]) if row else 0.001
        
        # Calculate cost
        cost = (tokens / 1000.0) * price_per_1k
        return round(cost, 4)
    finally:
        conn.close()


def check_credits(user_email: str, required_amount: float = 0.01) -> bool:
    """Check if user has sufficient credits."""
    credits = get_user_credits(user_email)
    return credits["credits_remaining"] >= required_amount


def get_usage_history(user_email: str, limit: int = 50) -> List[Dict]:
    """Get user's recent usage history."""
    conn = _get_conn()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT usage_type, amount, tokens_used, model, endpoint, 
                   description, created_at
            FROM credit_usage_log
            WHERE user_email = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_email, limit))
        
        rows = cursor.fetchall()
        
        # Convert datetime to string
        for row in rows:
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
        
        return rows
    finally:
        conn.close()


def add_credits(user_email: str, amount: float, description: str = "Manual credit addition"):
    """Add credits to a user's account (admin function)."""
    conn = _get_conn()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE user_credits
            SET ai_credits = ai_credits + %s,
                updated_at = NOW()
            WHERE user_email = %s
        """, (amount, user_email))
        
        conn.commit()
        
        log.info("Credits added",
                 user=user_email,
                 amount=amount,
                 description=description)
    finally:
        conn.close()
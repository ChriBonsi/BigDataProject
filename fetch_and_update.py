import os
import requests
from sqlalchemy import create_engine, text
from datetime import datetime

DB_URL = os.getenv("DATABASE_URL")
API_BASE_URL = "http://34.241.168.124:8000/api/v1/"
engine = create_engine(DB_URL)

def fetch_conversation_list():
    api_url = f"{API_BASE_URL}conversations?limit=50&offset=0"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            return response.json().get("conversations", [])
    except Exception:
        return []
    return []

def fetch_conversation_detail(conversation_id):
    api_url = f"{API_BASE_URL}conversations/{conversation_id}"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None

def save_to_db(data, updated_at_str, user_id):
    if not data:
        return

    conv_id = data.get("conversation_id")
    stats = data.get("llm_statistics", {})
    calls = data.get("llm_calls", [])
    
    models = stats.get("models_used", [])
    in_tokens = stats.get("total_input_tokens", 0)
    out_tokens = stats.get("total_output_tokens", 0)
    total_cost = stats.get("total_cost_usd", 0.0)
    duration = stats.get("total_duration_ms", 0)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_sessions (
                conv_id VARCHAR(100) PRIMARY KEY,
                user_id VARCHAR(100),
                total_calls INT,
                total_input_tokens INT,
                total_output_tokens INT,
                total_duration_ms INT,
                models_used TEXT,
                total_cost_usd NUMERIC(12, 6),
                updated_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS token_usage_history (
                id SERIAL PRIMARY KEY,
                conv_id VARCHAR(100),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                incremental_input_tokens INT,
                incremental_output_tokens INT
            );

            CREATE TABLE IF NOT EXISTS llm_calls (
                call_id INT PRIMARY KEY,
                conv_id VARCHAR(100),
                call_type VARCHAR(100),
                model VARCHAR(100),
                input_tokens INT,
                output_tokens INT,
                cost_usd NUMERIC(12, 6),
                called_at TIMESTAMP
            );
        """))
        
        conn.execute(
            text("""
                INSERT INTO api_sessions (
                    conv_id, user_id, total_calls, total_input_tokens, 
                    total_output_tokens, total_duration_ms, models_used, 
                    total_cost_usd, updated_at
                ) VALUES (
                    :conv_id, :user_id, :total_calls, :in_tokens, 
                    :out_tokens, :duration, :models, :total_cost, :updated_at
                )
                ON CONFLICT (conv_id) DO UPDATE SET
                    total_calls = EXCLUDED.total_calls,
                    total_input_tokens = EXCLUDED.total_input_tokens,
                    total_output_tokens = EXCLUDED.total_output_tokens,
                    total_duration_ms = EXCLUDED.total_duration_ms,
                    total_cost_usd = EXCLUDED.total_cost_usd,
                    updated_at = EXCLUDED.updated_at;
            """),
            {
                "conv_id": conv_id,
                "user_id": user_id,
                "total_calls": stats.get("total_calls"),
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "duration": duration,
                "models": ",".join(models),
                "total_cost": total_cost,
                "updated_at": updated_at_str
            }
        )

        conn.execute(
            text("""
                INSERT INTO token_usage_history (conv_id, incremental_input_tokens, incremental_output_tokens)
                VALUES (:conv_id, :in_tokens, :out_tokens)
            """),
            {"conv_id": conv_id, "in_tokens": in_tokens, "out_tokens": out_tokens}
        )

        for call in calls:
            conn.execute(
                text("""
                    INSERT INTO llm_calls (
                        call_id, conv_id, call_type, model, 
                        input_tokens, output_tokens, cost_usd, called_at
                    ) VALUES (
                        :cid, :cid, :ctype, :mod, :it, :ot, :cost, :dat
                    ) ON CONFLICT (call_id) DO NOTHING;
                """),
                {
                    "cid": call.get("id"),
                    "cid": conv_id,
                    "ctype": call.get("call_type"),
                    "mod": call.get("llm_model"),
                    "it": call.get("input_tokens"),
                    "ot": call.get("output_tokens"),
                    "cost": call.get("cost_usd"),
                    "dat": call.get("called_at")
                }
            )

def sync_conversations():
    conversations = fetch_conversation_list()
    if not conversations:
        return

    with engine.connect() as conn:
        for conv in conversations:
            user_id = conv.get("user_id", "Null")
            conv_id = conv.get("conversation_id")
            api_updated_at = conv.get("updated_at")

            result = conn.execute(
                text("SELECT updated_at FROM api_sessions WHERE conv_id = :cid"),
                {"cid": conv_id}
            ).fetchone()

            needs_fetch = False
            if result is None:
                needs_fetch = True
            else:
                db_updated_at = result[0].isoformat() if result[0] else ""
                if api_updated_at[:26] != db_updated_at[:26]:
                    needs_fetch = True

            if needs_fetch:
                detail_data = fetch_conversation_detail(conv_id)
                if detail_data:
                    save_to_db(detail_data, api_updated_at, user_id)

if __name__ == "__main__":
    sync_conversations()
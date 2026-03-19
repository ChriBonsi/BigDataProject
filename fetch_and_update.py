import os
import requests
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL)

MODEL_PRICING = {
    "gpt-4.1": {"input": 0.01 / 1000, "output": 0.03 / 1000},
    "gpt-4o": {"input": 0.005 / 1000, "output": 0.015 / 1000},
    "gpt-3.5-turbo": {"input": 0.002 / 1000, "output": 0.004 / 1000},
    # TODO: Add more models
}

def fetch_data():
    api_url = os.getenv("API_ENDPOINT", "https://api.com/data")
    response = requests.get(api_url)
    if response.status_code == 200:
        return response.json()
    return None

def calculate_cost(models, input_tokens, output_tokens):
    if not models:
        return 0.0, 0.0, 0.0
    
    primary_model = models[0]
    rates = MODEL_PRICING.get(primary_model, {"input": 0.0, "output": 0.0})
    
    input_cost = input_tokens * rates["input"]
    output_cost = output_tokens * rates["output"]
    total_cost = input_cost + output_cost
    
    return input_cost, output_cost, total_cost

def save_to_db(data):
    if not data:
        return

    stats = data.get("statistics", {})
    models = stats.get("models_used", [])
    in_tokens = stats.get("total_input_tokens", 0)
    out_tokens = stats.get("total_output_tokens", 0)

    in_cost, out_cost, total_cost = calculate_cost(models, in_tokens, out_tokens)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_sessions (
                session_id VARCHAR(100) PRIMARY KEY,
                status VARCHAR(50),
                total_calls INT,
                total_input_tokens INT,
                total_output_tokens INT,
                total_duration_ms INT,
                models_used TEXT,
                input_cost_usd NUMERIC(10, 6),
                output_cost_usd NUMERIC(10, 6),
                total_cost_usd NUMERIC(10, 6)
            )
        """))
        
        conn.execute(
            text("""
                INSERT INTO api_sessions (
                    session_id, status, total_calls, total_input_tokens, 
                    total_output_tokens, total_duration_ms, models_used, 
                    input_cost_usd, output_cost_usd, total_cost_usd
                ) VALUES (
                    :session_id, :status, :total_calls, :in_tokens, 
                    :out_tokens, :duration, :models, 
                    :in_cost, :out_cost, :total_cost
                )
                ON CONFLICT (session_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    total_calls = EXCLUDED.total_calls,
                    total_duration_ms = EXCLUDED.total_duration_ms
            """),
            {
                "session_id": data.get("session_id"),
                "status": data.get("status"),
                "total_calls": stats.get("total_calls"),
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "duration": stats.get("total_duration_ms"),
                "models": ",".join(models),
                "in_cost": in_cost,
                "out_cost": out_cost,
                "total_cost": total_cost
            }
        )

if __name__ == "__main__":
    data = fetch_data()
    save_to_db(data)
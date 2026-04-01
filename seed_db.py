import os
import random
import uuid
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL)

MODEL_PRICING = {
    "gpt-4.1": {"input": 0.01 / 1000, "output": 0.03 / 1000},
    "gpt-4o": {"input": 0.005 / 1000, "output": 0.015 / 1000},
    "gpt-3.5-turbo": {"input": 0.002 / 1000, "output": 0.004 / 1000},
}

def generate_fake_data(num_sessions=10):
    models = list(MODEL_PRICING.keys())
    sessions = []
    
    for _ in range(num_sessions):
        s_id = f"screening_{uuid.uuid4().hex[:12]}"
        model = [random.choice(models)]
        
        # Incremental steps for history
        steps = random.randint(2, 5)
        current_in = 0
        current_out = 0
        
        for i in range(steps):
            inc_in = random.randint(500, 2000)
            inc_out = random.randint(200, 800)
            current_in += inc_in
            current_out += inc_out
            
            sessions.append({
                "session_id": s_id,
                "status": "completed" if i == steps - 1 else "in_progress",
                "statistics": {
                    "total_calls": i + 1,
                    "total_input_tokens": current_in,
                    "total_output_tokens": current_out,
                    "total_duration_ms": random.randint(10000, 90000),
                    "models_used": model
                }
            })
    return sessions

def calculate_cost(models, input_tokens, output_tokens):
    primary_model = models[0]
    rates = MODEL_PRICING.get(primary_model, {"input": 0.0, "output": 0.0})
    in_cost = input_tokens * rates["input"]
    out_cost = output_tokens * rates["output"]
    return in_cost, out_cost, in_cost + out_cost

def run_seed():
    data_list = generate_fake_data()
    
    with engine.begin() as conn:
        # Create Tables
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_sessions (
                session_id VARCHAR(100) PRIMARY KEY,
                status VARCHAR(50),
                total_calls INT,
                total_input_tokens INT,
                total_output_tokens INT,
                total_duration_ms INT,
                models_used TEXT,
                input_cost_usd NUMERIC(12, 6),
                output_cost_usd NUMERIC(12, 6),
                total_cost_usd NUMERIC(12, 6)
            );
            CREATE TABLE IF NOT EXISTS token_usage_history (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(100),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                incremental_input_tokens INT,
                incremental_output_tokens INT
            );
        """))

        for data in data_list:
            stats = data["statistics"]
            m = stats["models_used"]
            in_t, out_t = stats["total_input_tokens"], stats["total_output_tokens"]
            in_c, out_c, tot_c = calculate_cost(m, in_t, out_t)

            # Update session summary
            conn.execute(text("""
                INSERT INTO api_sessions (session_id, status, total_calls, total_input_tokens, 
                    total_output_tokens, total_duration_ms, models_used, input_cost_usd, 
                    output_cost_usd, total_cost_usd)
                VALUES (:sid, :st, :tc, :it, :ot, :dur, :mods, :ic, :oc, :total)
                ON CONFLICT (session_id) DO UPDATE SET 
                    total_input_tokens = EXCLUDED.total_input_tokens,
                    total_output_tokens = EXCLUDED.total_output_tokens,
                    total_cost_usd = EXCLUDED.total_cost_usd;
            """), {"sid": data["session_id"], "st": data["status"], "tc": stats["total_calls"], 
                   "it": in_t, "ot": out_t, "dur": stats["total_duration_ms"], 
                   "mods": ",".join(m), "ic": in_c, "oc": out_c, "total": tot_c})

            # Add to history
            conn.execute(text("""
                INSERT INTO token_usage_history (session_id, incremental_input_tokens, incremental_output_tokens)
                VALUES (:sid, :it, :ot)
            """), {"sid": data["session_id"], "it": in_t, "ot": out_t})

    print(f"Seed completato con successo: {len(data_list)} record inseriti.")

if __name__ == "__main__":
    run_seed()
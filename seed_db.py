import os
import random
import uuid
from sqlalchemy import create_engine, text
from datetime import datetime

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL)

def generate_fake_data(num_sessions=10):
    call_types = ["question_generation", "report_generation", "topic_importance_extraction", "idq_c2_coherence"]
    models = ["gpt-4.1-2025-04-14", "gpt-5.2-2025-12-11"]
    conversations = []
    
    call_id_counter = random.randint(1000, 5000)
    
    for _ in range(num_sessions):
        conv_id = f"conv_{uuid.uuid4().hex[:16]}"
        user_id = f"user_{random.randint(100, 999)}"
        updated_at = datetime.now().isoformat()
        
        num_calls = random.randint(2, 8)
        total_in = 0
        total_out = 0
        total_cost = 0.0
        total_duration = 0
        mods_used = set()
        calls = []
        
        for _ in range(num_calls):
            mod = random.choice(models)
            mods_used.add(mod)
            c_type = random.choice(call_types)
            c_in = random.randint(300, 4000)
            c_out = random.randint(30, 800)
            dur = random.randint(800, 5000)
            
            cost = (c_in * 0.005 / 1000) + (c_out * 0.015 / 1000)
            
            calls.append({
                "id": call_id_counter,
                "call_type": c_type,
                "llm_model": mod,
                "input_tokens": c_in,
                "output_tokens": c_out,
                "cost_usd": cost,
                "called_at": datetime.utcnow().isoformat()
            })
            call_id_counter += 1
            
            total_in += c_in
            total_out += c_out
            total_cost += cost
            total_duration += dur
            
        conversations.append({
            "conversation_id": conv_id,
            "user_id": user_id,
            "updated_at": updated_at,
            "llm_statistics": {
                "total_calls": num_calls,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "total_duration_ms": total_duration,
                "models_used": list(mods_used),
                "total_cost_usd": total_cost
            },
            "llm_calls": calls
        })
        
    return conversations

def run_seed():
    data_list = generate_fake_data()
    
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

        for data in data_list:
            stats = data["llm_statistics"]
            conv_id = data["conversation_id"]
            user_id = data["user_id"]
            updated_at_str = data["updated_at"]

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
                    "in_tokens": stats.get("total_input_tokens"),
                    "out_tokens": stats.get("total_output_tokens"),
                    "duration": stats.get("total_duration_ms"),
                    "models": ",".join(stats.get("models_used", [])),
                    "total_cost": stats.get("total_cost_usd"),
                    "updated_at": updated_at_str
                }
            )

            conn.execute(
                text("""
                    INSERT INTO token_usage_history (conv_id, incremental_input_tokens, incremental_output_tokens)
                    VALUES (:conv_id, :in_tokens, :out_tokens)
                """),
                {"conv_id": conv_id, "in_tokens": stats.get("total_input_tokens"), "out_tokens": stats.get("total_output_tokens")}
            )

            for call in data.get("llm_calls", []):
                conn.execute(
                    text("""
                        INSERT INTO llm_calls (
                            call_id, conv_id, call_type, model, 
                            input_tokens, output_tokens, cost_usd, called_at
                        ) VALUES (
                            :cid, :conv_id, :ctype, :mod, :it, :ot, :cost, :dat
                        ) ON CONFLICT (call_id) DO NOTHING;
                    """),
                    {
                        "cid": call.get("id"),
                        "conv_id": conv_id,
                        "ctype": call.get("call_type"),
                        "mod": call.get("llm_model"),
                        "it": call.get("input_tokens"),
                        "ot": call.get("output_tokens"),
                        "cost": call.get("cost_usd"),
                        "dat": call.get("called_at")
                    }
                )

if __name__ == "__main__":
    run_seed()
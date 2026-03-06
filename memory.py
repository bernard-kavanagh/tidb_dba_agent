"""
DBA Episodic Memory — LangChain / TiDBVectorStore Edition
----------------------------------------------------------
Vector-based recall system for the DBA Agent.
Stores and retrieves past incident resolutions using semantic similarity.

Uses: HuggingFaceEmbeddings('all-MiniLM-L6-v2') → 384-dim vectors
      TiDBVectorStore (langchain-tidb) for ANN search
"""

import os
import json
from dotenv import load_dotenv
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import TiDBVectorStore
from langchain_core.documents import Document
from mysql.connector import Error
from db_manager import db_manager

load_dotenv()

# ── Lazy-loaded components ───────────────────────────────────────────────────
_embeddings = None
_vectorstore = None


def _get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        print("🧠 Loading embedding model (all-MiniLM-L6-v2)...")
        _embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    return _embeddings


def _get_vectorstore() -> TiDBVectorStore:
    global _vectorstore
    if _vectorstore is None:
        host = os.getenv("TIDB_HOST")
        port = os.getenv("TIDB_PORT", "4000")
        user = os.getenv("TIDB_USER")
        password = os.getenv("TIDB_PASSWORD")
        database = os.getenv("TIDB_DATABASE", "dba_agent_db")
        ssl_ca = os.getenv("TIDB_SSL_CA", "")

        # Base URL — no SSL params in the query string (pymysql ignores them there)
        connection_string = (
            f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
        )

        # Pass SSL cert via SQLAlchemy connect_args so pymysql actually uses it
        engine_args = {}
        if ssl_ca:
            engine_args["connect_args"] = {
                "ssl": {"ca": ssl_ca}
            }

        _vectorstore = TiDBVectorStore(
            connection_string=connection_string,
            embedding_function=_get_embeddings(),
            table_name="dba_episodic_memory",
            distance_strategy="cosine",
            engine_args=engine_args,
        )
    return _vectorstore


class DBAMemory:
    """Episodic memory for the DBA Agent — stores and recalls past fixes."""

    # ── Recall ────────────────────────────────────────────────────────────────

    def recall(self, error_description: str, min_confidence: float = 0.5, limit: int = 3) -> list:
        """
        Searches for semantically similar past incidents.

        Args:
            error_description: Natural language description of the current issue.
            min_confidence: Minimum similarity threshold (0–1). 1 = identical.
            limit: Max results to return.

        Returns:
            List of dicts with keys: incident_summary, resolution_sql,
            resolution_type, success_rating, confidence, before_time_ms, after_time_ms
        """
        try:
            vs = _get_vectorstore()
            results = vs.similarity_search_with_relevance_scores(
                error_description, k=limit
            )
            memories = []
            for doc, score in results:
                if score < min_confidence:
                    continue
                entry = doc.metadata.copy()
                entry["incident_summary"] = doc.page_content
                entry["confidence"] = round(score, 3)
                memories.append(entry)
            return memories
        except Exception as e:
            print(f"❌ Memory recall failed: {e}")
            return []

    # ── Save ──────────────────────────────────────────────────────────────────

    def save(
        self,
        incident_summary: str,
        resolution_sql: str,
        resolution_type: str = "INDEX_ADD",
        resolution_description: str = "",
        success_rating: float = 1.0,
        before_time_ms: int = 0,
        after_time_ms: int = 0,
        table_affected: str = "",
        query_affected: str = "",
        error_details: str = "",
    ) -> bool:
        """
        Saves a new incident resolution to episodic memory.

        The page_content is the human-readable summary (used for embedding).
        Metadata carries structured fields for retrieval.
        """
        text = f"{incident_summary} {error_details}".strip()
        metadata = {
            "resolution_sql": resolution_sql,
            "resolution_type": resolution_type,
            "resolution_description": resolution_description,
            "success_rating": success_rating,
            "before_time_ms": before_time_ms,
            "after_time_ms": after_time_ms,
            "table_affected": table_affected,
            "query_affected": query_affected,
            "error_details": error_details,
        }
        try:
            vs = _get_vectorstore()
            vs.add_texts(texts=[text], metadatas=[metadata])
            print(f"💾 Memory saved: {incident_summary[:60]}...")
            return True
        except Exception as e:
            print(f"❌ Memory save failed: {e}")
            return False

    # ── List all (for admin UI) ───────────────────────────────────────────────

    def list_all(self, limit: int = 20) -> list:
        """Returns recent memories from the raw TiDBVectorStore table (for admin UI).

        TiDBVectorStore owns this table with schema:
          id VARCHAR(36), embedding VECTOR, document TEXT, meta JSON, create_time TIMESTAMP
        All structured fields are stored as JSON inside `meta`.
        """
        sql = """
            SELECT
                id                                  AS memory_id,
                LEFT(document, 500)                 AS incident_summary,
                meta->>'$.resolution_type'          AS resolution_type,
                meta->>'$.resolution_sql'           AS resolution_sql,
                CAST(meta->>'$.success_rating'
                     AS DECIMAL(3,2))               AS success_rating,
                CAST(meta->>'$.before_time_ms'
                     AS SIGNED)                     AS before_time_ms,
                CAST(meta->>'$.after_time_ms'
                     AS SIGNED)                     AS after_time_ms,
                meta->>'$.table_affected'           AS table_affected,
                create_time                         AS created_at
            FROM dba_episodic_memory
            ORDER BY create_time DESC
            LIMIT %s
        """
        return db_manager.execute(sql, params=(limit,))


# Global instance
dba_memory = DBAMemory()


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mem = DBAMemory()
    print("🔍 Testing recall: 'slow query on orders table'")
    results = mem.recall("slow query on orders table")
    if results:
        for r in results:
            print(f"  ✅ {r['incident_summary']} (confidence: {r['confidence']})")
            print(f"     Fix: {r.get('resolution_sql', 'n/a')}")
    else:
        print("  ℹ️  No memories found (run seed_data.py first)")

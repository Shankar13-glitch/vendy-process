"""
core/semantic_matcher.py
Matches invoice service descriptions to tariff rules using ChromaDB + sentence-transformers.

Example:
  Invoice says  : "Servicio de Atraque"
  Tariff has    : "Berth", "Arrival Berthing", "Atraque"
  Matcher finds : best match + confidence score
"""

import logging
from typing import Optional
import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────

MODEL_NAME = r"C:\Scorpio\models\all-MiniLM-L6-v2"  # Local, fast, ~90MB
COLLECTION_NAME  = "tariff_services"
MIN_CONFIDENCE   = 0.75                  # Below this → flag for agent review


# ─────────────────────────────────────────────────────────────────────
# TARIFF SERVICE DEFINITIONS
# These are the canonical service types per port.
# Add new ports/services here as you expand.
# The "text" field should include synonyms in English/Spanish for best matching.
# ─────────────────────────────────────────────────────────────────────

TARIFF_SERVICES = [
    # ── Algeciras ──────────────────────────────────────────────────
    {"id": "algeciras_berth", "port": "Algeciras", "service_type": "berth",
     "text": "berth berthing mooring atraque arrival moor tie up secure vessel alongside dock port entry incoming tug assistance"},
    {"id": "algeciras_unberth", "port": "Algeciras", "service_type": "unberth",
     "text": "unberth unberthing unmooring desatraque departure cast off release vessel leave dock port exit outgoing tug assistance"},
    {"id": "algeciras_shift", "port": "Algeciras", "service_type": "shift",
     "text": "shift shifting enmienda cambio move berth to berth reposition vessel within port shifting operation"},

    # ── Ceuta ──────────────────────────────────────────────────────
    {"id": "ceuta_berth", "port": "Ceuta", "service_type": "berth",
     "text": "berth berthing mooring atraque arrival moor tie up secure vessel alongside dock port entry incoming tug assistance"},
    {"id": "ceuta_unberth", "port": "Ceuta", "service_type": "unberth",
     "text": "unberth unberthing unmooring desatraque departure cast off release vessel leave dock port exit outgoing tug assistance"},
    {"id": "ceuta_displacement", "port": "Ceuta", "service_type": "displacement",
     "text": "displacement desplazamiento tug travel mobilization repositioning fee tug moving from base to vessel dead run travel charge"},
    {"id": "ceuta_outport_surcharge", "port": "Ceuta", "service_type": "outport_surcharge",
     "text": "outport surcharge bay area zona bahia exterior outside port limits anchorage additional charge extra fee outer anchorage"},

    # ── Guaymas ────────────────────────────────────────────────────
    {"id": "guaymas_arrival", "port": "Guaymas", "service_type": "arrival",
     "text": "arrival berthing atraque entrada mooring incoming berth tie up secure vessel dock port entry servicio atraque"},
    {"id": "guaymas_departure", "port": "Guaymas", "service_type": "departure",
     "text": "departure unberthing desatraque salida unmooring outgoing cast off release vessel leave dock port exit servicio desatraque"},
    {"id": "guaymas_shift", "port": "Guaymas", "service_type": "shift",
     "text": "shift shifting enmienda cambio move berth to berth reposition vessel within port shifting operation maniobra"},
]



# ─────────────────────────────────────────────────────────────────────
# MATCHER CLASS
# ─────────────────────────────────────────────────────────────────────

class SemanticMatcher:
    """
    Loads tariff service definitions into ChromaDB.
    Matches incoming invoice descriptions to the closest tariff service.
    """

    def __init__(self, persist_directory: str = "./chroma_db"):
        self.persist_directory = persist_directory
        # Initialize embedding function
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME
        )
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self._load_collection()

    def _load_collection(self):
        """Load or create the tariff services collection."""
        collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

        # Only populate if empty
        if collection.count() == 0:
            logger.info("Populating tariff services into ChromaDB...")
            collection.add(
                ids       = [s["id"]   for s in TARIFF_SERVICES],
                documents = [s["text"] for s in TARIFF_SERVICES],
                metadatas = [{"port": s["port"], "service_type": s["service_type"]}
                             for s in TARIFF_SERVICES],
            )
            logger.info(f"Loaded {len(TARIFF_SERVICES)} service definitions.")
        else:
            logger.info(f"Collection ready with {collection.count()} entries.")

        return collection

    def match(
        self,
        description: str,
        port: Optional[str] = None,
        n_results: int = 3,
    ) -> dict:
        """
        Match an invoice service description to the closest tariff service.

        Args:
            description : Raw text from invoice (e.g. "Servicio de Atraque")
            port        : Optional port filter (e.g. "guaymas"). Case-insensitive.
            n_results   : Number of candidates to retrieve

        Returns:
            dict with best match, confidence, and alternatives
        """
        # Normalize port name to match metadata (Title Case) for filtering
        where = None
        if port:
            normalized_port = port.strip().title()
            where = {"port": normalized_port}

        results = self.collection.query(
            query_texts = [description],
            n_results   = n_results,
            where       = where,
        )

        # Handle empty results
        if not results["ids"][0]:
            return {
                "matched":      False,
                "service_type": None,
                "port":         port,
                "confidence":   0.0,
                "verdict":      "NO_MATCH",
                "alternatives": [],
            }

        # ChromaDB cosine distance → similarity (1 - distance)
        distances  = results["distances"][0]
        metadatas  = results["metadatas"][0]
        documents  = results["documents"][0]

        best_distance  = distances[0]
        best_meta      = metadatas[0]
        confidence     = round(1 - best_distance, 4)

        verdict = "MATCH" if confidence >= MIN_CONFIDENCE else "LOW_CONFIDENCE"

        alternatives = [
            {
                "service_type": metadatas[i]["service_type"],
                "port":         metadatas[i]["port"],
                "confidence":   round(1 - distances[i], 4),
                "text":         documents[i],
            }
            for i in range(1, len(distances))
        ]

        return {
            "matched":      confidence >= MIN_CONFIDENCE,
            "service_type": best_meta["service_type"],
            "port":         best_meta["port"],
            "confidence":   confidence,
            "verdict":      verdict,
            "alternatives": alternatives,
        }

    def reset(self):
        """Clear and reload the collection (use when tariff data changes)."""
        self.client.delete_collection(COLLECTION_NAME)
        self.collection = self._load_collection()
        logger.info("Collection reset and reloaded.")


# ─────────────────────────────────────────────────────────────────────
# SELF TEST
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # This will download the model (~90MB) on the first run
    matcher = SemanticMatcher()

    test_cases = [
        ("Servicio de Atraque",              "guaymas"), # Test case insensitivity
        ("Servicio de Desatraque",           "Guaymas"),
        ("Berting operation",                "Guaymas"), # Typo tolerance
        ("Unberting operation",              "Guaymas"),
        ("Displacement fee",                 "Ceuta"),
        ("Outport surcharge bay area",       "Ceuta"),
        ("Atraque zona industrial",          "Algeciras"),
        ("Desatraque",                       "Algeciras"),
        ("Shifting between berths",          "Algeciras"),
        ("Port towage arrival service",      None),    # No port filter
    ]

    print("\n" + "=" * 65)
    print("SEMANTIC MATCHER — TEST RESULTS")
    print("=" * 65)

    for description, port in test_cases:
        result = matcher.match(description, port=port)
        status = "✅" if result["matched"] else "⚠️ "
        print(f"\n  Input : '{description}' (port={port})")
        print(f"  Match : {result['service_type']} | Confidence: {result['confidence']} {status}")
        if result["alternatives"]:
            alt = result["alternatives"][0]
            print(f"  Alt   : {alt['service_type']} ({alt['confidence']})")

            
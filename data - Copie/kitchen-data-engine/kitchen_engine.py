import torch
import chromadb
from sentence_transformers import SentenceTransformer
from kitchen_data import RecipeSessionManager, TTSTextSanitizer

class KitchenDataEngine:
    def __init__(self, db_path: str = "./kitchen_db"):
        # Detect hardware acceleration
        self.device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        
        # Load embedder and persistent vector database locally
        self.model = SentenceTransformer("BAAI/bge-small-en-v1.5", device=self.device)
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        
        # Connect to collections
        self.recipes_col = self.chroma_client.get_collection("recipes")
        self.substitutions_col = self.chroma_client.get_collection("substitutions")
        self.nutrition_col = self.chroma_client.get_collection("nutrition")
        
        # In-memory session manager
        self.session = RecipeSessionManager()

    def search(self, user_query: str, top_k: int = 2) -> dict:
        """Routes query and returns retrieved RAG context directly in memory."""
        q_lower = user_query.lower()
        
        if any(k in q_lower for k in ["calories", "protein", "fat", "carbs", "nutrition", "macros"]):
            target_col = self.nutrition_col
            domain = "Nutrition"
        elif any(k in q_lower for k in ["substitute", "instead", "replace", "alternative"]):
            target_col = self.substitutions_col
            domain = "Substitution"
        else:
            target_col = self.recipes_col
            domain = "Recipe"

        with torch.inference_mode():
            embedding = self.model.encode([user_query], normalize_embeddings=True).tolist()
            
        results = target_col.query(query_embeddings=embedding, n_results=top_k)
        
        return {
            "domain": domain,
            "documents": results["documents"][0] if results["documents"] else [],
            "metadatas": results["metadatas"][0] if results["metadatas"] else []
        }

    def sanitize_for_tts(self, raw_llm_text: str) -> str:
        """Sanitizes LLM output text for speech synthesis."""
        return TTSTextSanitizer.sanitize(raw_llm_text)
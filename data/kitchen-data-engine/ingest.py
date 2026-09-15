import json
import torch
import chromadb
from datasets import load_dataset
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
print(f"Using compute device: {device}")

model = SentenceTransformer("BAAI/bge-small-en-v1.5", device=device)
chroma_client = chromadb.PersistentClient(path="./kitchen_db")

def safe_parse(val):
    if isinstance(val, list): return val
    if isinstance(val, str):
        try: return json.loads(val)
        except: return [val]
    return []

def ingest_collection(dataset_name, collection_name, text_fn, meta_fn, batch_size=1000, limit=None, data_files=None):
    print(f"\n--- Ingesting {dataset_name} into '{collection_name}' ---")
    collection = chroma_client.get_or_create_collection(name=collection_name, embedding_function=None)
    
    if data_files:
        ds = load_dataset(dataset_name, data_files=data_files, split="train")
    else:
        ds = load_dataset(dataset_name, split="train")
        
    if limit: 
        ds = ds.select(range(min(limit, len(ds))))
    
    ids_buf, texts_buf, metas_buf = [], [], []
    for idx, row in enumerate(tqdm(ds, total=len(ds))):
        ids_buf.append(f"{collection_name}_{idx}")
        texts_buf.append(text_fn(row))
        metas_buf.append(meta_fn(row))
        
        if len(ids_buf) >= batch_size or idx == len(ds) - 1:
            with torch.inference_mode():
                embeddings = model.encode(texts_buf, batch_size=256, convert_to_numpy=True, normalize_embeddings=True).tolist()
            collection.add(ids=ids_buf, documents=texts_buf, embeddings=embeddings, metadatas=metas_buf)
            ids_buf, texts_buf, metas_buf = [], [], []

if __name__ == "__main__":
    # 1. Substitutions (~25k rows total)
    ingest_collection(
        "oraclemangle/historical-culinary-substitutions", 
        "substitutions",
        lambda r: f"Original Ingredient: {r.get('ingredient','')}. Substitute: {r.get('substitute','')}.",
        lambda r: {"original": str(r.get("ingredient","")), "substitute": str(r.get("substitute",""))},
        data_files="substitutions.jsonl"
    )

    # 2. Nutrition (capped at 50k rows)
    ingest_collection(
        "omid5/usda-fdc-foods-cleaned", 
        "nutrition",
        lambda r: f"Food Item: {r.get('food_item','')}. Serving: {r.get('serving_amount',100)}{r.get('serving_unit','g')}. Calories: {r.get('Energy','N/A')} kcal, Protein: {r.get('Protein','N/A')}g, Fat: {r.get('Total lipid (fat)','N/A')}g, Carbs: {r.get('Carbohydrate, by difference','N/A')}g.",
        lambda r: {"food_item": str(r.get("food_item",""))},
        limit=50000
    )

    # 3. Recipes (capped at 50k rows)
    ingest_collection(
        "Kaiser1308/CookingRecipes", 
        "recipes",
        lambda r: f"Title: {r.get('title','Untitled')}. Ingredients: {', '.join(safe_parse(r.get('NER','')))}. Directions: {' '.join(safe_parse(r.get('directions','')))}",
        lambda r: {"title": str(r.get("title","")), "source": str(r.get("link",""))},
        limit=50000
    )

    print("\nIngestion complete! Local './kitchen_db' is ready.")

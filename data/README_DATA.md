Directory Structure
Ensure your project directory is organized as follows:

kitchen-assistant/
├── kitchen_db/              # Generated ChromaDB storage (created on ingestion)
├── ingest.py                # Dataset downloader and vector embedder script
├── kitchen_data.py          # Session manager & TTS sanitizer classes
├── kitchen_engine.py        # In-process RAG & search interface module
├── test_engine.py           # Verification script
└── requirements.txt         # Python dependencies


Prerequisites & Hardware Requirements
OS: Linux (Ubuntu 22.04+ recommended), macOS, or Windows WSL2.
Python: Version 3.10, 3.11, or 3.12.
Storage: At least 15 GB of free disk space (for dataset downloading, embeddings, and vector store).
Compute acceleration (Optional, but recommended):
NVIDIA GPU: CUDA 11.8+ or 12.1+
Apple Silicon: MPS (Metal Performance Shaders)

Step-by-Step Installation
Step 1: Create a Python Virtual Environment
Open a terminal in the project root directory and create a clean virtual environment:

python3 -m venv venv
source venv/bin/activate


(On Windows PowerShell, use .\venv\Scripts\Activate.ps1)

Step 2: Install Dependencies
Create a requirements.txt file with the following contents:

torch
chromadb
datasets
sentence-transformers
tqdm


Install the packages:

pip install --upgrade pip
pip install -r requirements.txt


Step 3: Populate Local Vector Database (ingest.py)
Use ingest.py to pull datasets directly from Hugging Face, compute vector embeddings using BAAI/bge-small-en-v1.5, and persist them locally into ./kitchen_db.

python ingest.py


Step 4: Core Engine Modules
kitchen_data.py
Contains helper logic for active cooking sessions and cleaning markdown before sending output to Text-to-Speech (TTS).


kitchen_engine.py
Provides the primary interface for your team to load vector stores and search context directly in Python.


Step 5: Verification & Integration Test
Use test_engine.py to verify that all components operate as expected on the host system:

python test_engine.py


Main Pipeline Integration Example
Here is how other modules (such as STT, LLM generation, or TTS audio output) interact with KitchenDataEngine inside the primary execution loop:



Python
from kitchen_engine import KitchenDataEngine

# Initialize once at system boot
engine = KitchenDataEngine()

def process_user_voice_input(user_speech_text: str):
    # 1. Query vector database for relevant domain context
    rag_context = engine.search(user_speech_text)
    
    # 2. Inject context into LLM Prompt
    system_prompt = f"Use this background culinary data to answer: {rag_context['documents']}"
    raw_llm_response = my_llm_model.generate(system_prompt, user_speech_text)
    
    # 3. Sanitize markdown formatting before piping to Text-to-Speech
    tts_ready_speech = engine.sanitize_for_tts(raw_llm_response)
    
    # 4. Play audio via TTS engine
    my_tts_model.speak(tts_ready_speech)



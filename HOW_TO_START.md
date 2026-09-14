Start the Backend
cd ~/Documents/GitHub/KitchenBuddy/KitchenBuddy/happy-bite/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000


Start the Frontend
cd happy-bite/frontend
npm install
npm run dev

Start AI model 
ollama serve


3 terminals
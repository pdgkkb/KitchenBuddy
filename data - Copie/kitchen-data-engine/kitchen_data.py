import re
from typing import Dict, List, Optional, Any

class RecipeSessionManager:
    """Tracks the active cooking session state, allowing step-by-step navigation

    and state persistence across user voice commands.
    """

    def __init__(self):
        self._active_recipe: Optional[Dict[str, Any]] = None
        self._current_step_index: int = 0

    @property
    def is_active(self) -> bool:
        """Check if a recipe is currently active in the session."""
        return self._active_recipe is not None

    def start_recipe(self, recipe_data: Dict[str, Any]) -> str:
        """Initialize a new recipe session.

        Expects a dictionary with keys: 'title', 'ingredients', 'directions'.
        """
        self._active_recipe = {
            "title": recipe_data.get("title", "Untitled Recipe"),
            "ingredients": recipe_data.get("ingredients", []),
            "directions": recipe_data.get("directions", []),
            "source": recipe_data.get("source", "")
        }
        self._current_step_index = 0
        
        directions = self._active_recipe["directions"]
        if not directions:
            return f"Started recipe for {self._active_recipe['title']}, but no step instructions were found."
            
        return f"Starting recipe for {self._active_recipe['title']}. Step 1: {directions[0]}"

    def get_current_step(self) -> str:
        """Returns the current active instruction step."""
        if not self.is_active:
            return "No active recipe session. Please select a recipe first."
            
        directions = self._active_recipe["directions"]
        if not directions:
            return "This recipe has no listed steps."
            
        total_steps = len(directions)
        step_text = directions[self._current_step_index]
        return f"Step {self._current_step_index + 1} of {total_steps}: {step_text}"

    def next_step(self) -> str:
        """Advances to the next step in the active recipe."""
        if not self.is_active:
            return "No active recipe session. Please select a recipe first."
            
        directions = self._active_recipe["directions"]
        if self._current_step_index + 1 < len(directions):
            self._current_step_index += 1
            return self.get_current_step()
        else:
            return "You have reached the final step of this recipe! Bon appétit!"

    def previous_step(self) -> str:
        """Goes back to the previous step in the active recipe."""
        if not self.is_active:
            return "No active recipe session."
            
        if self._current_step_index > 0:
            self._current_step_index -= 1
            return self.get_current_step()
        else:
            return f"You are already at the first step. {self.get_current_step()}"

    def get_ingredients(self) -> str:
        """Returns the ingredient list for the active recipe."""
        if not self.is_active:
            return "No active recipe session."
            
        ingredients = self._active_recipe["ingredients"]
        if isinstance(ingredients, list):
            ing_str = ", ".join(ingredients)
        else:
            ing_str = str(ingredients)
            
        return f"Ingredients for {self._active_recipe['title']}: {ing_str}"

    def clear_session(self) -> str:
        """Clears the active session state."""
        self._active_recipe = None
        self._current_step_index = 0
        return "Recipe session cleared."


class TTSTextSanitizer:
    """Regex-based text cleaner to convert raw markdown or structured LLM text

    into natural, clean spoken English for Text-to-Speech engines.
    """

    # Common fraction mappings for spoken audio
    FRACTIONS = {
        r'\b1/2\b': 'one half',
        r'\b1/3\b': 'one third',
        r'\b2/3\b': 'two thirds',
        r'\b1/4\b': 'one quarter',
        r'\b3/4\b': 'three quarters',
        r'\b1/8\b': 'one eighth',
        r'\b3/8\b': 'three eighths',
    }

    @classmethod
    def sanitize(cls, text: str) -> str:
        if not text:
            return ""

        cleaned = text

        # 1. Remove Markdown headers (e.g. ## Title)
        cleaned = re.sub(r'^#+\s*', '', cleaned, flags=re.MULTILINE)

        # 2. Convert Markdown links [text](url) -> text
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)

        # 3. Strip Markdown formatting: bold, italics, inline code (`code`, **bold**, *italic*)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        cleaned = re.sub(r'[*_]{1,3}(.*?)[*_]{1,3}', r'\1', cleaned)

        # 4. Remove list prefixes (bullets like -, *, or numbered prefixes like 1.)
        cleaned = re.sub(r'^\s*[-*+]\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*\d+\.\s+', '', cleaned, flags=re.MULTILINE)

        # 5. Expand symbols and units for natural speech
        cleaned = re.sub(r'°[FF]', ' degrees Fahrenheit', cleaned)
        cleaned = re.sub(r'°[CC]', ' degrees Celsius', cleaned)
        cleaned = re.sub(r'°', ' degrees ', cleaned)
        cleaned = re.sub(r'&', ' and ', cleaned)

        # 6. Replace fractions with spoken words
        for pattern, replacement in cls.FRACTIONS.items():
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

        # 7. Strip special characters (keep words, numbers, and basic speech punctuation)
        cleaned = re.sub(r'[^\w\s.,?!\'"-]', '', cleaned)

        # 8. Collapse multiple spaces and newlines into single spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        return cleaned
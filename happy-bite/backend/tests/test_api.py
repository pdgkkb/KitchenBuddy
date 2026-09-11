"""Run from backend/:  python -m pytest -q

No network, no keys: the model is a fake that returns what we tell it,
including the malformed replies a real one sends on a bad day."""

import json

import pytest
from fastapi.testclient import TestClient

from app.catalog import ingredients
from app.importer import ImportErrorPublic, _guard, match_line, plain_recipe, read_page
from app.main import app
from app.recipes import clean_recipe

KNOWN = ingredients()

GOOD = {
    "name": "Courgette fritters", "minutes": 25, "complexity": 1, "types": ["dinner", "brunch"],
    "cuisine": "Greek", "serves": 4,
    "needs": [{"id": "courgette", "qty": 500, "prep": "grated"}, {"id": "egg", "qty": 2},
              {"id": "dragonfruit", "qty": 1}, {"id": "flour", "qty": -40}, {"id": "salt", "qty": 4}],
    "seasoning": [{"id": "oregano", "qty": 2, "essential": False}],
    "extras": ["a handful of dill"],
    "steps": [{"do": "Grate and salt the courgettes.", "minutes": 10, "why": "Salt pulls the water out."},
              {"do": "Fry spoonfuls.", "heat": "Medium-high", "cue": "Edges golden", "minutes": 8},
              {"minutes": 3}],
}


class FakeLLM:
    name = "fake:test"

    def __init__(self, reply=None, chunks=("Keep ", "going.")):
        self.reply, self.chunks, self.seen = reply, chunks, []

    async def json(self, system, user, schema, max_tokens=0):
        self.seen.append((system, user))
        return self.reply

    async def stream(self, system, messages, max_tokens=0):
        self.seen.append((system, messages))
        for c in self.chunks:
            yield c


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.llm = None
        app.state.images = None
        app.state.speech = None
        yield c


# ---------------------------------------------------------------- the gate

def test_invented_ids_and_bad_quantities_are_dropped():
    r = clean_recipe(GOOD, KNOWN)
    assert [n["id"] for n in r["needs"]] == ["courgette", "egg"]
    assert {s["id"] for s in r["seasoning"]} == {"oregano", "salt"}    # salt moved, not lost
    assert r["types"] == ["dinner"]                                    # "brunch" isn't a meal type
    assert len(r["steps"]) == 2                                        # the step with no action is gone
    assert r["extras"] == ["a handful of dill"]


@pytest.mark.parametrize("bad", [
    None, {}, {"name": "x"}, {"name": "x", "steps": "fry it"},
    {"name": "x", "needs": [{"id": "unicorn", "qty": 1}], "steps": [{"do": "a"}, {"do": "b"}]},
    {"name": "x", "needs": [{"id": "egg", "qty": 2}], "steps": [{"do": "only one"}]},
])
def test_malformed_replies_are_rejected(bad):
    assert clean_recipe(bad, KNOWN) is None


# ---------------------------------------------------------------- endpoints

def test_status_reports_what_is_off(client):
    s = client.get("/api/status").json()
    assert s == {"chat": False, "recipes": False, "images": False, "voice": False, "model": None, "local": False}


def test_generate_is_503_without_a_model(client):
    assert client.post("/api/recipes/generate", json={"brief": "x"}).status_code == 503


def test_generate_passes_through_the_gate(client):
    fake = FakeLLM(GOOD)
    app.state.llm = fake
    res = client.post("/api/recipes/generate", json={
        "brief": "use the courgettes", "serves": 2,
        "stock": [{"id": "courgette", "qty": 700, "unit": "g", "daysLeft": 1}],
    })
    assert res.status_code == 200
    assert res.json()["recipe"]["origin"] == "assistant"
    system, _ = fake.seen[0]
    assert "courgette (700 g, 1 days left)" in system and "Cook for 2" in system


def test_generate_rejects_nonsense(client):
    app.state.llm = FakeLLM({"name": "nope", "steps": []})
    assert client.post("/api/recipes/generate", json={"brief": "x"}).status_code == 502


def test_chat_streams_and_knows_the_step(client):
    fake = FakeLLM()
    app.state.llm = fake
    recipe = {"name": "Gratin", "steps": [{"do": "Heat the pan."}, {"do": "Add courgettes."}]}
    with client.stream("POST", "/api/chat", json={
        "messages": [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "Is this right?"}],
        "context": {"mode": "cooking", "recipe": recipe, "step": 1, "serves": 3},
    }) as res:
        events = [json.loads(line[6:]) for line in res.iter_lines() if line.startswith("data: ")]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Keep going."
    assert events[-1]["type"] == "done"
    system, _ = fake.seen[0]
    assert 'step 2 of 2: "Add courgettes."' in system and "Cooking for 3" in system


def test_understand_keeps_only_valid_filters(client):
    app.state.llm = FakeLLM({"mealType": "dinner", "maxMinutes": 20, "cuisine": "Martian",
                             "mustUse": "courgette", "avoid": ["beef", "unicorn"], "understood": "quick"})
    out = client.post("/api/understand", json={"text": "quick courgette dinner, no beef"}).json()
    assert out["filters"] == {"mealType": "dinner", "maxMinutes": 20, "mustUse": "courgette"}
    assert out["avoid"] == ["beef"]


# ---------------------------------------------------------------- links

PAGE = """<html><head><title>Best ratatouille</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","name":"x"},
 {"@type":["Recipe"],"name":"Ratatouille","totalTime":"PT1H10M","recipeYield":["4","4 servings"],
  "image":{"url":"https://example.com/r.jpg"},
  "recipeIngredient":["2 courgettes","400 g tomatoes","1 onion","2 tbsp olive oil","1 tsp smoked paprika","2 bay leaves"],
  "recipeInstructions":[{"@type":"HowToSection","itemListElement":[
     {"@type":"HowToStep","text":"Soften the onion for 10 minutes."},
     {"@type":"HowToStep","text":"Add the rest and simmer 40 min."}]}]}
]}</script></head><body><p>Life story…</p></body></html>"""


def test_json_ld_is_read_from_a_graph():
    page = read_page(PAGE, "https://www.example.com/ratatouille")
    assert page["kind"] == "structured" and page["site"] == "example.com"
    assert page["minutes"] == 70 and page["serves"] == 4
    assert page["image"] == "https://example.com/r.jpg"
    assert page["steps"] == ["Soften the onion for 10 minutes.", "Add the rest and simmer 40 min."]


def test_plain_import_maps_what_it_can_and_keeps_the_rest():
    r = clean_recipe(plain_recipe(read_page(PAGE, "https://example.com/r"), KNOWN), KNOWN, origin="link")
    assert {n["id"]: n["qty"] for n in r["needs"]} == {"courgette": 400, "tomato": 400, "onion": 1, "oliveoil": 3.0}
    assert [s["id"] for s in r["seasoning"]] == ["paprika"]
    assert r["extras"] == ["2 bay leaves"]
    assert [s["minutes"] for s in r["steps"]] == [10, 40]


def test_word_match_does_not_find_oats_in_goat():
    assert match_line("150 g goat's cheese", KNOWN)["id"] == "goat"


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://localhost:8000", "file:///etc/passwd",
                                 "http://192.168.1.1/"])
def test_private_addresses_are_refused(url):
    with pytest.raises(ImportErrorPublic):
        _guard(url)

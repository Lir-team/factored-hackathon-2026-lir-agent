"""The labeled set of the typed decisions, and its leak-free split.

Every seed (one situation) is in one split with all its paraphrases, so a model is never
scored on a rewording of a sentence its threshold was chosen on. Seeds are assigned by a
hash of their id, alternating within each intent: deterministic, and stratified by class.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
SEEDS = REPO / "data" / "eval" / "decisions" / "seeds.yaml"
INTENTS = (
    "cargo_no_reconocido",
    "cobro_indebido",
    "consulta_movimiento",
    "otra_queja",
    "fuera_de_alcance",
)
VALIDATION, TEST = "validation", "test"


@dataclass(frozen=True)
class Item:
    item_id: str
    seed_id: str
    split: str
    lang: str
    text: str
    intent: str
    human: bool
    theft: bool

    @property
    def language(self) -> str:
        """es, pt or mixed (portuñol): the groups compared for equity."""
        return "mixed" if self.lang == "mixed" else self.lang.split("-")[0]


def _rank(seed_id: str) -> str:
    return hashlib.sha256(seed_id.encode()).hexdigest()


def load(path: Path = SEEDS) -> list[Item]:
    """Every paraphrase as an item, with its seed's labels and split."""
    seeds = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [s["id"] for s in seeds]
    if len(ids) != len(set(ids)):
        raise ValueError("Seed ids must be unique")
    split_of: dict[str, str] = {}
    for intent in INTENTS:
        group = sorted((s["id"] for s in seeds if s["intent"] == intent), key=_rank)
        for position, seed_id in enumerate(group):
            split_of[seed_id] = VALIDATION if position % 2 == 0 else TEST
    items = []
    for seed in seeds:
        if seed["intent"] not in INTENTS:
            raise ValueError(f"{seed['id']}: unknown intent {seed['intent']!r}")
        for n, text in enumerate(seed["texts"], start=1):
            items.append(
                Item(
                    item_id=f"{seed['id']}-{n}",
                    seed_id=seed["id"],
                    split=split_of[seed["id"]],
                    lang=seed["lang"],
                    text=text,
                    intent=seed["intent"],
                    human=bool(seed["human"]),
                    theft=bool(seed["theft"]),
                )
            )
    return items

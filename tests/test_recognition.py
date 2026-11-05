import json

import cv2

from padbox.recognition.matcher import Matcher


def test_ranked_identity_and_alternatives(store, fixtures):
    matcher = Matcher(store.references())
    image = cv2.imread(str(fixtures["references"].parent / "icons" / "1.png"))
    result = matcher.match(image)
    assert result.candidates[0].monster_id == 900001
    assert result.candidates[0].score > 0.99
    assert len(result.candidates) == 3
    assert result.high_confidence
    assert result.candidates[0].reference_icon.endswith("1.png")


def test_similar_variants_cannot_be_high_confidence(store, fixtures):
    refs = store.references()
    refs[1]["icon"] = refs[0]["icon"]
    matcher = Matcher(refs)
    image = cv2.imread(refs[0]["icon"])
    result = matcher.match(image)
    assert result.candidates[0].score == result.candidates[1].score
    assert not result.high_confidence


def test_missing_and_tiny_reference_set(store):
    assert not Matcher([]).match(cv2.imread(store.references()[0]["icon"])).candidates
    refs = store.references()[:1]
    result = Matcher(refs).match(cv2.imread(refs[0]["icon"]))
    assert not result.high_confidence


def test_large_library_shortlist_keeps_distinct_exact_match(store):
    refs = store.references()
    extra = [{**refs[1], "id": 800000 + i, "name": f"Distractor {i}"} for i in range(120)]
    result = Matcher(extra + refs).match(cv2.imread(refs[0]["icon"]))
    assert result.candidates[0].monster_id == refs[0]["id"]
    assert result.high_confidence


def test_stackable_reference_cannot_be_batch_accepted(store):
    refs = store.references()
    refs[0]["raw"] = json.dumps({"stackable": True})
    result = Matcher(refs).match(cv2.imread(refs[0]["icon"]))
    assert result.candidates[0].monster_id == refs[0]["id"]
    assert not result.high_confidence
    assert "quantity" in result.reason

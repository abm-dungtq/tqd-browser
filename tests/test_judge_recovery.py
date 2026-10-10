"""The judge picks the button that only closes a pop-up and reads the dialog shown after Post."""

import json

import pytest

from tqd_automation.judge import (
    POST_OUTCOMES,
    CompositeJudge,
    HeuristicJudge,
    Verdict,
)

TIP = ["Bỏ qua", "Đã hiểu"]


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("TQD_AUTOMATION_DIR", str(tmp_path / "automation_data"))


class FakeTypeSafe:
    def __init__(self, dismiss=None, outcome=None):
        self.dismiss, self.outcome = dismiss, outcome

    def pick_dismiss(self, platform, overlay, buttons):
        return self.dismiss

    def post_outcome(self, platform, dialog):
        return self.outcome


def test_heuristic_picks_a_got_it_button():
    v = HeuristicJudge().pick_dismiss("tiktok", "Mẹo mới", TIP)
    assert (v.label, v.source) == ("1", "heuristic") and v.confidence >= 0.8


def test_heuristic_picks_nothing_it_does_not_know():
    assert HeuristicJudge().pick_dismiss("tiktok", "?", ["Tiếp tục", "Bật"]).label == "none"


def test_known_tip_skips_typesafe():
    judge = CompositeJudge(HeuristicJudge(), FakeTypeSafe(Verdict("0", 0.99, "typesafe")))
    assert judge.pick_dismiss("tiktok", "Mẹo", TIP).label == "1"


def test_confident_typesafe_picks_an_unknown_button(tmp_path):
    t = Verdict("0", 0.91, "typesafe")
    judge = CompositeJudge(HeuristicJudge(), FakeTypeSafe(t))
    v = judge.pick_dismiss("tiktok", "Hủy bỏ bài đăng này?", ["Tiếp tục chỉnh sửa", "Hủy bỏ"])
    assert v == t
    log = (tmp_path / "automation_data").glob("judge_*.jsonl")
    lines = [json.loads(x) for f in log for x in f.read_text("utf-8").splitlines()]
    assert lines[-1]["kind"] == "dismiss" and lines[-1]["buttons"] == [
        "Tiếp tục chỉnh sửa",
        "Hủy bỏ",
    ]


def test_unsure_typesafe_picks_nothing():
    judge = CompositeJudge(HeuristicJudge(), FakeTypeSafe(Verdict("0", 0.5, "typesafe")))
    assert judge.pick_dismiss("tiktok", "?", ["Tiếp tục", "Bật"]).label == "none"


def test_heuristic_outcome_knows_only_sent_phrases():
    assert HeuristicJudge().post_outcome("x", "Bài đăng của bạn đã được gửi").label == "published"
    assert HeuristicJudge().post_outcome("tiktok", "Kiểm tra nội dung").label == "UNKNOWN"


def test_confident_typesafe_outcome_wins_and_unsure_falls_back():
    sure = CompositeJudge(
        HeuristicJudge(), FakeTypeSafe(outcome=Verdict("processing", 0.9, "typesafe"))
    )
    assert sure.post_outcome("tiktok", "Video đang được xử lý").label == "processing"
    unsure = CompositeJudge(
        HeuristicJudge(), FakeTypeSafe(outcome=Verdict("processing", 0.6, "typesafe"))
    )
    assert unsure.post_outcome("tiktok", "Video đang được xử lý").label == "UNKNOWN"


class FakeClient:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        key = next(iter(questions))
        return type("Result", (), {"answers": {key: self.answer}})()


def _answer(**fields):
    return type("Answer", (), fields)()


def test_typesafe_dismiss_question_offers_each_button_and_none():
    pytest.importorskip("typesafe_sdk")
    from tqd_automation.judge import TypeSafeJudge

    client = FakeClient(_answer(choice="0", confidence=0.93))
    v = TypeSafeJudge(client).pick_dismiss("tiktok", "Hủy bỏ bài đăng này?", ["Tiếp tục", "Hủy"])
    assert (v.label, v.confidence) == ("0", 0.93)
    state, questions = client.calls[0]
    assert state["buttons"] == {"0": "Tiếp tục", "1": "Hủy"}
    assert set(questions["dismiss"].criteria) == {"0", "1", "none"}


def test_typesafe_outcome_question_and_errors():
    pytest.importorskip("typesafe_sdk")
    from tqd_automation.judge import TypeSafeJudge

    client = FakeClient(_answer(choice="rejected", confidence=0.88))
    assert TypeSafeJudge(client).post_outcome("tiktok", "Lỗi").label == "rejected"
    assert set(client.calls[0][1]["outcome"].criteria) == set(POST_OUTCOMES)

    class Broken:
        def system_one(self, state, questions):
            raise RuntimeError("quota exceeded")

    assert TypeSafeJudge(Broken()).pick_dismiss("x", "?", ["A"]).label == "none"
    assert TypeSafeJudge(Broken()).post_outcome("x", "?").label == "UNKNOWN"


def test_blocked_click_clears_the_popup_and_retries_once(monkeypatch):
    from src.mcp_server.server import FoxProfileError
    from tqd_automation.guards import Guards
    from tqd_automation.platforms import common

    monkeypatch.setattr(common, "pause", lambda *a: None)

    class Browser:
        def __init__(self):
            self.clicks, self.tip = [], True

        def click(self, selector):
            self.clicks.append(selector)
            if selector == "#caption" and self.tip:
                raise FoxProfileError("covered")
            if selector == "[data-tqd-candidate='1']":
                self.tip = False

        def evaluate(self, script):
            return {"result": {"text": "Mẹo", "buttons": TIP} if self.tip else None}

    b = Browser()
    run = common.BaseRun("p", "Chào", [], b, CompositeJudge(HeuristicJudge()), Guards())
    run.click("#caption")
    assert b.clicks == ["#caption", "[data-tqd-candidate='1']", "#caption"]

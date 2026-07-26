"""Pin comment vote counting against concurrent voters.

The route used to read comment.likes into Python, add one, and write it back.
Two voters landing together both read the same value and both wrote the same
value, so one vote vanished — and with four gunicorn workers serving the site,
overlapping votes are ordinary traffic, not an edge case. A double-click from
one session was worse: the second request tried to insert a second row for the
same (comment_id, session_id) and hit the unique index, which the handler turned
into a 500.

The vote row and the counters now move together in SQL. These tests drive the
statements the route uses through real concurrent connections, so a regression
to read-modify-write fails them rather than merely looking different.

Needs a live database. Run:
    sudo docker exec ttw_api python3 -m pytest tests/test_comment_vote_atomicity.py -v
 or sudo docker exec ttw_api python3 tests/test_comment_vote_atomicity.py
"""
import os
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _PytestStub:
        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

from sqlalchemy import text

from app.database.models import SessionLocal, Comment, CommentVote, Trend, utc_now
from app.api.routes import (
    _SQL_VOTE_APPLY_COUNTS,
    _SQL_VOTE_TOGGLE_OFF,
    _SQL_VOTE_UPSERT,
)

_comment_id = None
_trend_id = None


def _cast_vote(comment_id, session_id, vote_type):
    """The route's decision path, verbatim, on its own connection."""
    db = SessionLocal()
    try:
        toggled_off = db.execute(_SQL_VOTE_TOGGLE_OFF, {
            "cid": comment_id, "sid": session_id, "vt": vote_type,
        }).first()

        if toggled_off:
            dl = -1 if vote_type == 1 else 0
            dd = -1 if vote_type == -1 else 0
        else:
            applied = db.execute(_SQL_VOTE_UPSERT, {
                "cid": comment_id, "sid": session_id, "vt": vote_type,
                "now": utc_now(),
            }).first()
            if applied is None:
                dl = dd = 0
            elif applied[0]:
                dl = 1 if vote_type == 1 else 0
                dd = 1 if vote_type == -1 else 0
            else:
                dl, dd = vote_type, -vote_type

        counts = db.execute(_SQL_VOTE_APPLY_COUNTS, {
            "cid": comment_id, "dl": dl, "dd": dd,
        }).first()
        db.commit()
        return counts[0], counts[1]
    finally:
        db.close()


def _counts():
    db = SessionLocal()
    try:
        c = db.get(Comment, _comment_id)
        db.refresh(c)
        return c.likes, c.dislikes
    finally:
        db.close()


def _vote_rows():
    db = SessionLocal()
    try:
        return db.query(CommentVote).filter(
            CommentVote.comment_id == _comment_id).count()
    finally:
        db.close()


def setup_function(_=None):
    """A throwaway comment per test, on a throwaway trend."""
    global _comment_id, _trend_id
    db = SessionLocal()
    try:
        trend = Trend(
            title="vote-atomicity probe",
            cluster_id=f"vote-probe-{os.getpid()}-{id(object())}",
        )
        db.add(trend)
        db.flush()
        comment = Comment(
            trend_id=trend.id, user_name="probe", session_id="owner",
            content="probe", likes=0, dislikes=0, status="approved",
        )
        db.add(comment)
        db.commit()
        _trend_id, _comment_id = trend.id, comment.id
    finally:
        db.close()


def teardown_function(_=None):
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM comment_votes WHERE comment_id = :c"),
                   {"c": _comment_id})
        db.execute(text("DELETE FROM comments WHERE id = :c"), {"c": _comment_id})
        db.execute(text("DELETE FROM trends WHERE id = :t"), {"t": _trend_id})
        db.commit()
    finally:
        db.close()


def test_concurrent_distinct_voters_all_counted():
    """The lost update: 20 voters at once must produce 20 likes, not fewer."""
    voters = 20
    barrier = threading.Barrier(voters)

    def vote(n):
        barrier.wait()               # maximise overlap
        _cast_vote(_comment_id, f"session-{n}", 1)

    threads = [threading.Thread(target=vote, args=(i,)) for i in range(voters)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    likes, dislikes = _counts()
    assert likes == voters, f"{voters - likes} vote(s) lost to a concurrent write"
    assert dislikes == 0
    assert _vote_rows() == voters


def test_same_session_double_click_does_not_double_count():
    """A repeated identical vote must be idempotent, not a second like."""
    _cast_vote(_comment_id, "sess", 1)
    likes_after_first, _ = _counts()
    assert likes_after_first == 1

    # second identical click toggles off, third turns it back on
    _cast_vote(_comment_id, "sess", 1)
    assert _counts() == (0, 0)
    assert _vote_rows() == 0

    _cast_vote(_comment_id, "sess", 1)
    assert _counts() == (1, 0)
    assert _vote_rows() == 1


def test_concurrent_identical_votes_never_exceed_one():
    """Racing clicks from one session must not break the unique index."""
    attempts = 8
    barrier = threading.Barrier(attempts)
    errors = []

    def vote():
        barrier.wait()
        try:
            _cast_vote(_comment_id, "same-session", 1)
        except Exception as e:                       # the old path raised here
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=vote) for _ in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent identical votes raised: {errors[:2]}"
    likes, _ = _counts()
    rows = _vote_rows()
    assert rows <= 1, "the unique index must hold"
    assert likes == rows, (
        f"counter ({likes}) disagrees with stored votes ({rows})"
    )


def test_flipping_a_vote_moves_one_from_each_counter():
    _cast_vote(_comment_id, "flipper", 1)
    assert _counts() == (1, 0)

    _cast_vote(_comment_id, "flipper", -1)
    assert _counts() == (0, 1), "a flip must decrement the old side too"
    assert _vote_rows() == 1, "flipping must not create a second row"

    _cast_vote(_comment_id, "flipper", 1)
    assert _counts() == (1, 0)


def test_counters_never_go_negative():
    """Historical drift must not render as '-1 likes' on the page."""
    db = SessionLocal()
    try:
        db.execute(text("UPDATE comments SET likes = 0 WHERE id = :c"),
                   {"c": _comment_id})
        db.execute(text(
            "INSERT INTO comment_votes (comment_id, session_id, vote_type, created_at)"
            " VALUES (:c, 'ghost', 1, :now)"
        ), {"c": _comment_id, "now": utc_now()})
        db.commit()
    finally:
        db.close()

    # removing a vote the counter never recorded would take it below zero
    _cast_vote(_comment_id, "ghost", 1)
    likes, dislikes = _counts()
    assert likes >= 0 and dislikes >= 0, f"negative counter: {likes}/{dislikes}"


def test_mixed_concurrent_votes_match_stored_rows():
    """Whatever the interleaving, the counters must describe the vote table."""
    total = 24
    barrier = threading.Barrier(total)

    def vote(n):
        barrier.wait()
        _cast_vote(_comment_id, f"mixed-{n}", 1 if n % 2 == 0 else -1)

    threads = [threading.Thread(target=vote, args=(i,)) for i in range(total)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    likes, dislikes = _counts()
    db = SessionLocal()
    try:
        stored_likes = db.query(CommentVote).filter(
            CommentVote.comment_id == _comment_id,
            CommentVote.vote_type == 1).count()
        stored_dislikes = db.query(CommentVote).filter(
            CommentVote.comment_id == _comment_id,
            CommentVote.vote_type == -1).count()
    finally:
        db.close()

    assert (likes, dislikes) == (stored_likes, stored_dislikes), (
        f"counters {likes}/{dislikes} disagree with rows "
        f"{stored_likes}/{stored_dislikes}"
    )


def _run_standalone() -> int:
    failures = []
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        ran += 1
        setup_function()
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        finally:
            teardown_function()
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

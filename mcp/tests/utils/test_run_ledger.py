"""Unit tests for the cross-run create/email ledgers (utils.run_ledger, issue #673).

The ledger is the only fleet-wide cross-run idempotency mechanism (Magnite and
TripleLift have no list/lookup endpoint to reconcile against), so its contract
is pinned tightly here:

* round-trip: record_create -> lookup_create returns the compact summary;
* strict no-op when CUTLASS_RUN_WORKDIR is unset or not an existing dir;
* corrupt/torn JSONL lines are tolerated and skipped on read;
* multi-append: the latest successful record wins;
* the email fingerprint mirrors the Go orchestrator's normalization.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils import run_ledger


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path))
    return tmp_path


SUCCESS_RESULT = {
    "success": True,
    "deal_id": "DL-123",
    "internal_deal_id": 987654,
    "external_deal_id": "IX177756700801987654",
    "deal_url": "https://ssp.example/deal/987654",
    "requires_manual_remediation": True,
    "quality_flags": ["fee_creation_error"],
    "deal": {
        "id": 987654,
        "externalDealID": "IX177756700801987654",
        "name": "Elcano_OX_TTD_Test",
        "secret_payload": {"token": "nope"},
    },
    "preparation": {"huge": "payload"},
    "warnings": ["w1"],
}


class TestCreateLedgerRoundTrip:
    def test_record_then_lookup(self, workdir):
        run_ledger.record_create("indexexchange", "Elcano_IX_TTD_Test", SUCCESS_RESULT)

        record = run_ledger.lookup_create("indexexchange", "Elcano_IX_TTD_Test")
        assert record is not None
        assert record["ssp"] == "indexexchange"
        assert record["deal_name"] == "Elcano_IX_TTD_Test"
        assert record["success"] is True
        assert record["deal_id"] == "DL-123"
        assert record["internal_deal_id"] == 987654
        # The IX-prefixed external deal id survives the compact summary — a
        # resumed create must still put the trader-facing id on the sheet.
        assert record["external_deal_id"] == "IX177756700801987654"
        assert record["deal_url"] == "https://ssp.example/deal/987654"
        assert record["requires_manual_remediation"] is True
        assert record["quality_flags"] == ["fee_creation_error"]
        assert record["deal"] == {
            "id": 987654,
            "externalDealID": "IX177756700801987654",
            "name": "Elcano_OX_TTD_Test",
        }
        # Compact summary only — payloads and non-whitelisted keys never land.
        assert "preparation" not in record
        assert "warnings" not in record
        raw = (workdir / run_ledger.CREATES_FILENAME).read_text(encoding="utf-8")
        assert "secret_payload" not in raw

    @pytest.mark.usefixtures("workdir")
    def test_lookup_misses_other_ssp_and_name(self):
        run_ledger.record_create("indexexchange", "DealA", SUCCESS_RESULT)
        assert run_ledger.lookup_create("openx", "DealA") is None
        assert run_ledger.lookup_create("indexexchange", "DealB") is None

    def test_lookup_ignores_non_success_records(self, workdir):
        path = workdir / run_ledger.CREATES_FILENAME
        path.write_text(
            json.dumps({"ssp": "openx", "deal_name": "DealA", "success": False}) + "\n",
            encoding="utf-8",
        )
        assert run_ledger.lookup_create("openx", "DealA") is None

    @pytest.mark.usefixtures("workdir")
    def test_multi_append_latest_record_wins(self):
        run_ledger.record_create("openx", "DealA", {"success": True, "deal_id": "first"})
        run_ledger.record_create("openx", "DealA", {"success": True, "deal_id": "second"})
        run_ledger.record_create("openx", "DealB", {"success": True, "deal_id": "other"})

        record = run_ledger.lookup_create("openx", "DealA")
        assert record is not None
        assert record["deal_id"] == "second"

    def test_corrupt_and_torn_lines_are_skipped(self, workdir):
        path = workdir / run_ledger.CREATES_FILENAME
        good = json.dumps({"ssp": "xandr", "deal_name": "DealX", "success": True, "deal_id": 7})
        path.write_bytes(
            b"not json at all\n"
            + b'{"ssp": "xandr", "deal_name": "torn", "succ\n'  # torn write
            + b"\xff\xfe garbage bytes\n"
            + b'"a bare json string"\n'
            + good.encode("utf-8")
            + b"\n"
        )
        record = run_ledger.lookup_create("xandr", "DealX")
        assert record is not None
        assert record["deal_id"] == 7

    @pytest.mark.usefixtures("workdir")
    def test_resumed_response_shape(self):
        run_ledger.record_create("magnite", "DealM", SUCCESS_RESULT)
        record = run_ledger.lookup_create("magnite", "DealM")
        response = run_ledger.resumed_create_response(record)
        assert response["success"] is True
        assert response["resumed"] is True
        assert "previous run" in response["note"]
        assert "DealM" in response["note"]
        assert response["internal_deal_id"] == 987654
        assert response["external_deal_id"] == "IX177756700801987654"
        assert response["requires_manual_remediation"] is True
        assert response["quality_flags"] == ["fee_creation_error"]


PARTIAL_RESULT = {
    "success": False,
    "internal_deal_id": 424242,
    "external_deal_id": "IX177756700801424242",
    "deal_url": "https://ssp.example/deal/424242",
    "deal": {"internalDealID": 424242, "externalDealID": "IX177756700801424242", "name": "DealP"},
    "error": {"message": "seat attach failed"},
}


class TestPartialCreateLedger:
    """Partial creates (deal EXISTS, follow-up step failed — issue #708):
    recorded distinctly from confirmed successes so a cross-run retry blocks
    the blind re-create instead of resuming as a success."""

    @pytest.mark.usefixtures("workdir")
    def test_record_then_lookup_partial(self):
        run_ledger.record_partial_create("indexexchange", "DealP", PARTIAL_RESULT, reason="seat_attach_failed")

        record = run_ledger.lookup_partial_create("indexexchange", "DealP")
        assert record is not None
        assert record["ssp"] == "indexexchange"
        assert record["deal_name"] == "DealP"
        assert record["success"] is False
        assert record["partial"] is True
        assert record["reason"] == "seat_attach_failed"
        assert record["internal_deal_id"] == 424242
        assert record["external_deal_id"] == "IX177756700801424242"
        # Compact summary only — the error payload never lands in the ledger.
        assert "error" not in record

    @pytest.mark.usefixtures("workdir")
    def test_partial_record_never_resumes_as_success(self):
        run_ledger.record_partial_create("indexexchange", "DealP", PARTIAL_RESULT, reason="seat_attach_failed")
        # lookup_create is the resume path — a partial hit there would replay
        # the create as a confirmed success, hiding the missing seat.
        assert run_ledger.lookup_create("indexexchange", "DealP") is None

    @pytest.mark.usefixtures("workdir")
    def test_success_record_not_returned_by_partial_lookup(self):
        run_ledger.record_create("indexexchange", "DealS", SUCCESS_RESULT)
        assert run_ledger.lookup_partial_create("indexexchange", "DealS") is None

    @pytest.mark.usefixtures("workdir")
    def test_partial_lookup_misses_other_ssp_and_name(self):
        run_ledger.record_partial_create("indexexchange", "DealP", PARTIAL_RESULT)
        assert run_ledger.lookup_partial_create("openx", "DealP") is None
        assert run_ledger.lookup_partial_create("indexexchange", "DealQ") is None

    def test_unset_env_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
        run_ledger.record_partial_create("indexexchange", "DealP", PARTIAL_RESULT)
        assert run_ledger.lookup_partial_create("indexexchange", "DealP") is None
        assert list(tmp_path.iterdir()) == []

    def test_empty_keys_are_noop(self, workdir):
        run_ledger.record_partial_create("", "DealP", PARTIAL_RESULT)
        run_ledger.record_partial_create("indexexchange", "", PARTIAL_RESULT)
        assert not (workdir / run_ledger.CREATES_FILENAME).exists()


class TestSubmitMarkerLedger:
    """PRE-POST submit markers (issue #708): written BEFORE a non-idempotent
    create POST goes out, resolved only by a definite outcome — a confirmed
    create, a partial create, or an explicit did-not-create resolution. An
    unresolved marker means the deal MAY exist (ambiguous transport / process
    death mid-POST) and a blind cross-run re-create must be refused."""

    @pytest.mark.usefixtures("workdir")
    def test_record_then_lookup_unresolved(self):
        run_ledger.record_create_submitted("magnite", "DealM", "magnite-abc123")

        record = run_ledger.lookup_unresolved_submit("magnite", "DealM")
        assert record is not None
        assert record["ssp"] == "magnite"
        assert record["deal_name"] == "DealM"
        assert record["submitted"] is True
        assert record["success"] is False
        assert record["prepared_deal_id"] == "magnite-abc123"

    @pytest.mark.usefixtures("workdir")
    def test_confirmed_create_resolves_marker(self):
        run_ledger.record_create_submitted("magnite", "DealM")
        run_ledger.record_create("magnite", "DealM", SUCCESS_RESULT)
        assert run_ledger.lookup_unresolved_submit("magnite", "DealM") is None
        # …and the confirmed create resumes normally.
        assert run_ledger.lookup_create("magnite", "DealM") is not None

    @pytest.mark.usefixtures("workdir")
    def test_explicit_resolution_releases_marker(self):
        run_ledger.record_create_submitted("magnite", "DealM")
        run_ledger.resolve_create_submitted("magnite", "DealM", reason="create POST returned HTTP 422")
        assert run_ledger.lookup_unresolved_submit("magnite", "DealM") is None

    @pytest.mark.usefixtures("workdir")
    def test_partial_create_resolves_marker(self):
        run_ledger.record_create_submitted("indexexchange", "DealM")
        run_ledger.record_partial_create("indexexchange", "DealM", PARTIAL_RESULT)
        assert run_ledger.lookup_unresolved_submit("indexexchange", "DealM") is None

    @pytest.mark.usefixtures("workdir")
    def test_resubmit_after_resolution_is_unresolved_again(self):
        run_ledger.record_create_submitted("magnite", "DealM")
        run_ledger.resolve_create_submitted("magnite", "DealM")
        run_ledger.record_create_submitted("magnite", "DealM")
        assert run_ledger.lookup_unresolved_submit("magnite", "DealM") is not None

    @pytest.mark.usefixtures("workdir")
    def test_marker_never_leaks_into_create_or_partial_lookups(self):
        run_ledger.record_create_submitted("magnite", "DealM")
        assert run_ledger.lookup_create("magnite", "DealM") is None
        assert run_ledger.lookup_partial_create("magnite", "DealM") is None

    @pytest.mark.usefixtures("workdir")
    def test_lookup_misses_other_ssp_and_name(self):
        run_ledger.record_create_submitted("magnite", "DealM")
        assert run_ledger.lookup_unresolved_submit("openx", "DealM") is None
        assert run_ledger.lookup_unresolved_submit("magnite", "DealN") is None

    def test_unset_env_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
        run_ledger.record_create_submitted("magnite", "DealM")
        assert run_ledger.lookup_unresolved_submit("magnite", "DealM") is None
        assert list(tmp_path.iterdir()) == []

    def test_empty_keys_are_noop(self, workdir):
        run_ledger.record_create_submitted("", "DealM")
        run_ledger.record_create_submitted("magnite", "")
        run_ledger.resolve_create_submitted("", "DealM")
        assert not (workdir / run_ledger.CREATES_FILENAME).exists()


class TestFailSafeNoOps:
    def test_unset_env_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CUTLASS_RUN_WORKDIR", raising=False)
        run_ledger.record_create("openx", "DealA", SUCCESS_RESULT)
        assert run_ledger.lookup_create("openx", "DealA") is None
        run_ledger.record_email_sent("fp")
        assert run_ledger.email_already_sent("fp") is False
        assert list(tmp_path.iterdir()) == []

    def test_missing_dir_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CUTLASS_RUN_WORKDIR", str(tmp_path / "does-not-exist"))
        run_ledger.record_create("openx", "DealA", SUCCESS_RESULT)
        assert run_ledger.lookup_create("openx", "DealA") is None
        run_ledger.record_email_sent("fp")
        assert run_ledger.email_already_sent("fp") is False

    def test_blank_env_is_noop(self, monkeypatch):
        monkeypatch.setenv("CUTLASS_RUN_WORKDIR", "   ")
        run_ledger.record_create("openx", "DealA", SUCCESS_RESULT)
        assert run_ledger.lookup_create("openx", "DealA") is None

    def test_empty_keys_are_noop(self, workdir):
        run_ledger.record_create("", "DealA", SUCCESS_RESULT)
        run_ledger.record_create("openx", "", SUCCESS_RESULT)
        assert run_ledger.lookup_create("", "DealA") is None
        assert run_ledger.lookup_create("openx", "") is None
        assert not (workdir / run_ledger.CREATES_FILENAME).exists()


class TestEmailLedger:
    @pytest.mark.usefixtures("workdir")
    def test_send_once_round_trip(self):
        fp = run_ledger.email_fingerprint("ops@example.net", "Deal Sheet", "<p>Hi</p>")
        assert fp is not None
        assert run_ledger.email_already_sent(fp) is False
        run_ledger.record_email_sent(fp)
        assert run_ledger.email_already_sent(fp) is True

    def test_none_fingerprint_is_noop(self, workdir):
        run_ledger.record_email_sent(None)
        assert run_ledger.email_already_sent(None) is False
        assert not (workdir / run_ledger.EMAILS_FILENAME).exists()

    @pytest.mark.usefixtures("workdir")
    def test_task_key_is_body_independent_and_bound_to_task(self, monkeypatch):
        monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-123")
        first = run_ledger.task_email_key()
        second = run_ledger.task_email_key()
        assert first == second
        assert first is not None

        monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-456")
        assert run_ledger.task_email_key() != first

    @pytest.mark.usefixtures("workdir")
    def test_task_key_requires_an_explicit_task_id(self, monkeypatch):
        """A workdir alone is NOT a task identity.

        A host may point CUTLASS_RUN_WORKDIR at one directory shared by every
        run on the box (fleet's shared MCP client does). Keying the once-per-task
        invariant to that path collapsed every task onto one key, so the first
        send anywhere on the deployment suppressed every later send.
        """
        monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)
        assert run_ledger.task_email_key() is None

        monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "   ")
        assert run_ledger.task_email_key() is None

    def test_shared_workdir_never_suppresses_a_later_task(self, workdir, monkeypatch):
        """The regression, end to end: task A sends, task B must still send."""
        monkeypatch.delenv("CUTLASS_MOC_TASK_ID", raising=False)

        key_a = run_ledger.task_email_key()
        assert key_a is None
        run_ledger.record_email_sent(key_a)  # no identity => nothing recorded

        # A second task, same shared workdir, same inert key.
        assert run_ledger.email_already_sent(run_ledger.task_email_key()) is False
        assert not (workdir / run_ledger.EMAILS_FILENAME).exists()

    @pytest.mark.usefixtures("workdir")
    def test_task_scoped_send_once_still_holds(self, monkeypatch):
        """With a real task id the cross-run guard works exactly as before."""
        monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-abc")
        key = run_ledger.task_email_key()
        assert key is not None
        assert run_ledger.email_already_sent(key) is False
        run_ledger.record_email_sent(key)
        assert run_ledger.email_already_sent(key) is True

        # A different task on the same workdir is unaffected.
        monkeypatch.setenv("CUTLASS_MOC_TASK_ID", "task-xyz")
        assert run_ledger.email_already_sent(run_ledger.task_email_key()) is False


class TestEmailFingerprint:
    """Mirrors internal/agent/agent.go sendEmailFingerprint normalization."""

    def test_recipient_order_case_and_separator_invariant(self):
        a = run_ledger.email_fingerprint("A@x.com, b@x.com", "Subject", "<p>Body</p>")
        b = run_ledger.email_fingerprint("b@x.com;\na@x.com", "Subject", "<p>Body</p>")
        assert a == b

    def test_subject_case_and_whitespace_invariant(self):
        a = run_ledger.email_fingerprint("a@x.com", "  Deal Sheet ", "<p>Body</p>")
        b = run_ledger.email_fingerprint("a@x.com", "deal sheet", "<p>Body</p>")
        assert a == b

    def test_different_body_differs(self):
        a = run_ledger.email_fingerprint("a@x.com", "S", "<p>Body 1</p>")
        b = run_ledger.email_fingerprint("a@x.com", "S", "<p>Body 2</p>")
        assert a != b

    def test_cc_list_participates(self):
        a = run_ledger.email_fingerprint("a@x.com", "S", "<p>B</p>", cc_emails=["c@x.com"])
        b = run_ledger.email_fingerprint("a@x.com", "S", "<p>B</p>", cc_emails=["d@x.com"])
        c = run_ledger.email_fingerprint("a@x.com", "S", "<p>B</p>")
        assert a != b
        assert a != c

    def test_content_file_used_when_content_empty(self):
        a = run_ledger.email_fingerprint("a@x.com", "S", "", content_file="/tmp/email.html")
        b = run_ledger.email_fingerprint("a@x.com", "S", "", content_file="/tmp/other.html")
        assert a is not None
        assert a != b

    def test_unfingerprintable_args_return_none(self):
        assert run_ledger.email_fingerprint("", "S", "<p>B</p>") is None
        assert run_ledger.email_fingerprint("a@x.com", "", "<p>B</p>") is None
        assert run_ledger.email_fingerprint("a@x.com", "S", "", content_file=None) is None

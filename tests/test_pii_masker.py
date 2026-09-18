from src.privacy.pii_masker import PIIMasker


def test_masks_full_address_with_period_suffix_and_city_state_zip():
    masker = PIIMasker()

    masked, counts = masker.mask(
        "Ship To Address: 742 Evergreen St., Springfield, OR 97477"
    )

    assert "742 Evergreen" not in masked
    assert "Springfield, OR 97477" not in masked
    assert "Ship To Address: [REDACTED_ADDRESS]" in masked
    assert counts["addresses"] == 1


def test_masks_terrace_address_with_city_state_zip():
    masker = PIIMasker()

    masked, counts = masker.mask(
        "Ship To Address: 742 Evergreen Terrace, Springfield, OR 97477"
    )

    assert "742 Evergreen Terrace" not in masked
    assert "Ship To Address: [REDACTED_ADDRESS]" in masked
    assert counts["addresses"] == 1


def test_does_not_mask_tracking_id_as_phone_number():
    masker = PIIMasker()

    masked, counts = masker.mask("Tracking ID: 1Z9999999999999999")

    assert "1Z9999999999999999" in masked
    assert "[REDACTED_PHONE]" not in masked
    assert counts["phones"] == 0


def test_does_not_mask_spaced_ups_tracking_number_as_phone():
    masker = PIIMasker()

    masked, counts = masker.mask(
        "Tracking Number: 1Z 999 999 9999 9999 99\n" "Contact Phone: 555-432-8765"
    )

    assert "1Z 999 999 9999 9999 99" in masked
    assert "Contact Phone: [REDACTED_PHONE]" in masked
    assert counts["phones"] == 1


def test_does_not_mask_numeric_tracking_number_as_phone():
    masker = PIIMasker()

    masked, counts = masker.mask("Tracking Number: 7949 1122 3344")

    assert "7949 1122 3344" in masked
    assert "[REDACTED_PHONE]" not in masked
    assert counts["phones"] == 0

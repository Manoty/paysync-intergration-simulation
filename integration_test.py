"""
Full PaySync Integration Test
==============================
Runs Tixora and Scott flows against a live PaySync instance.
Tests the shared infrastructure — both systems using the same
PaySync API without interfering with each other.
"""

import uuid
import logging
from paysync_client import PaySyncClient, PaySyncError

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('integration_test')

PAYSYNC_BASE_URL = "http://127.0.0.1:8000"


def test_isolation_between_systems():
    """
    Both systems use the same external_reference format.
    Verify PaySync keeps them isolated via source_system.
    """
    print("\n" + "="*60)
    print("TEST: System isolation (same ref, different systems)")
    print("="*60)

    tixora = PaySyncClient(PAYSYNC_BASE_URL, 'tixora')
    scott  = PaySyncClient(PAYSYNC_BASE_URL, 'scott')

    # Both send the exact same external_reference
    shared_ref = f"REF_{uuid.uuid4().hex[:8].upper()}"

    tixora_result = tixora.initiate_payment(500, "254708374149", shared_ref)
    scott_result  = scott.initiate_payment(150, "254708374149", shared_ref)

    logger.info(f"Tixora reference: {tixora_result.reference}")
    logger.info(f"Scott reference:  {scott_result.reference}")

    # Must be different PaySync references
    if tixora_result.reference != scott_result.reference:
        logger.info("✅ PASS — Systems are isolated. Same ext ref, different payments.")
    else:
        logger.error("❌ FAIL — Systems are NOT isolated. Same reference returned!")


def test_list_filtering():
    """
    Verify that filtering by source_system returns only
    that system's payments — no cross-contamination.
    """
    print("\n" + "="*60)
    print("TEST: List filtering by source_system")
    print("="*60)

    client = PaySyncClient(PAYSYNC_BASE_URL, 'tixora')

    # Fetch only Tixora payments
    response = client.session.get(
        f"{PAYSYNC_BASE_URL}/api/v1/payments/?source_system=tixora",
        timeout=10,
    )
    data = response.json()

    payments = data['data']['payments']
    non_tixora = [p for p in payments if p['source_system'] != 'tixora']

    if not non_tixora:
        logger.info(f"✅ PASS — All {len(payments)} returned payments belong to tixora.")
    else:
        logger.error(f"❌ FAIL — {len(non_tixora)} non-tixora payments in tixora filter!")


def test_validation_errors():
    """
    Verify PaySync rejects invalid inputs cleanly.
    External systems receive structured error messages.
    """
    print("\n" + "="*60)
    print("TEST: Input validation")
    print("="*60)

    client = PaySyncClient(PAYSYNC_BASE_URL, 'tixora')

    # Bad phone number
    result = client.initiate_payment(500, "123", "VALIDATION_TEST_1")
    if not result.success:
        logger.info(f"✅ PASS — Bad phone rejected: {result.failure_reason}")
    else:
        logger.error("❌ FAIL — Bad phone was accepted!")

    # Amount below M-Pesa minimum
    result = client.initiate_payment(0, "254708374149", "VALIDATION_TEST_2")
    if not result.success:
        logger.info(f"✅ PASS — Zero amount rejected: {result.failure_reason}")
    else:
        logger.error("❌ FAIL — Zero amount was accepted!")

    # Amount above M-Pesa maximum
    result = client.initiate_payment(200000, "254708374149", "VALIDATION_TEST_3")
    if not result.success:
        logger.info(f"✅ PASS — Over-limit amount rejected: {result.failure_reason}")
    else:
        logger.error("❌ FAIL — Over-limit amount was accepted!")

    # Invalid source_system
    bad_client = PaySyncClient(PAYSYNC_BASE_URL, 'unknown_system')
    result = bad_client.initiate_payment(100, "254708374149", "VALIDATION_TEST_4")
    if not result.success:
        logger.info(f"✅ PASS — Unknown source_system rejected: {result.failure_reason}")
    else:
        logger.error("❌ FAIL — Unknown source_system was accepted!")


if __name__ == '__main__':
    print("\n" + "="*60)
    print("PaySync Integration Test Suite")
    print("="*60)
    print("Make sure PaySync server is running on localhost:8000\n")

    test_isolation_between_systems()
    test_list_filtering()
    test_validation_errors()

    print("\n" + "="*60)
    print("Integration tests complete.")
    print("="*60)
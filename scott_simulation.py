"""
Scott Integration Simulation
==============================
Simulates the Scott delivery platform's payment flow.

Scenario:
  Delivery confirmed → Scott charges customer for delivery fee
  Scott calls PaySync to collect delivery payment
  Scott marks delivery paid or triggers escalation flow
"""

import uuid
import logging
from dataclasses import dataclass
from paysync_client import PaySyncClient, PaySyncError

logger = logging.getLogger('scott')

PAYSYNC_BASE_URL = "http://127.0.0.1:8000"
SOURCE_SYSTEM    = "scott"


# ── Scott internal models ──────────────────────────────────────────────────────

@dataclass
class DeliveryOrder:
    """Simulates a Scott delivery record."""
    delivery_id:    str
    rider:          str
    destination:    str
    delivery_fee:   int
    phone:          str
    status:         str = 'awaiting_payment'   # awaiting_payment → paid | escalated
    paysync_ref:    str = None

    def mark_paid(self):
        self.status = 'paid'
        logger.info(
            f"✅ DELIVERY PAID | id={self.delivery_id} | "
            f"rider={self.rider} | fee=KES {self.delivery_fee}"
        )

    def escalate(self, reason):
        self.status = 'escalated'
        logger.warning(
            f"⚠️  DELIVERY ESCALATED | id={self.delivery_id} | reason={reason}"
        )

    def __str__(self):
        return (
            f"Delivery {self.delivery_id} | {self.destination} | "
            f"KES {self.delivery_fee} | Status: {self.status}"
        )


# ── Scott payment flow ─────────────────────────────────────────────────────────

class ScottPaymentFlow:
    """
    How Scott collects delivery payments via PaySync.

    Scott's flow differs from Tixora:
    - Smaller amounts (delivery fees vs ticket prices)
    - Escalation instead of cancellation on failure
    - Checks for existing payment before initiating (crash recovery)
    """

    def __init__(self):
        self.client = PaySyncClient(
            base_url=PAYSYNC_BASE_URL,
            source_system=SOURCE_SYSTEM,
        )

    def collect_delivery_fee(self, delivery: DeliveryOrder):
        """
        Collect payment for a completed delivery.

        Crash recovery built in:
        If Scott crashed after initiating payment, restarting
        and calling this method again will find the existing
        payment via idempotency — not create a duplicate.
        """
        logger.info(
            f"\n{'='*60}\n"
            f"SCOTT: Collecting delivery fee\n"
            f"  Delivery: {delivery.delivery_id}\n"
            f"  Rider:    {delivery.rider}\n"
            f"  Dest:     {delivery.destination}\n"
            f"  Fee:      KES {delivery.delivery_fee}\n"
            f"  Phone:    {delivery.phone}\n"
            f"{'='*60}"
        )

        # ── Initiate payment ──────────────────────────────────────────────────
        try:
            result = self.client.initiate_payment(
                amount=delivery.delivery_fee,
                phone_number=delivery.phone,
                external_reference=delivery.delivery_id,
            )
        except PaySyncError as e:
            logger.error(f"PaySync unavailable: {e}")
            delivery.escalate(f"Payment system unavailable: {e}")
            return delivery

        if not result.success:
            delivery.escalate(
                f"Payment initiation failed: {result.failure_reason}"
            )
            return delivery

        delivery.paysync_ref = result.reference
        logger.info(
            f"PaySync reference stored | "
            f"delivery={delivery.delivery_id} | ref={result.reference}"
        )

        logger.info("📱 STK Push sent. Waiting for customer to pay...")

        # ── Poll for outcome ──────────────────────────────────────────────────
        # Scott gives customers 90 seconds — shorter than Tixora
        # because delivery riders are waiting
        try:
            final = self.client.poll_until_complete(
                reference=result.reference,
                max_wait_seconds=90,
                poll_interval_seconds=5,
            )
        except PaySyncError as e:
            delivery.escalate(f"Status check failed: {e}")
            return delivery

        # ── React to outcome ──────────────────────────────────────────────────
        if final.status == 'success':
            delivery.mark_paid()

        elif final.status == 'failed':
            # Scott escalates rather than silently cancelling
            # A human reviews escalated deliveries
            delivery.escalate(
                final.failure_reason or "Customer did not complete payment."
            )

        else:
            # Timeout — payment may still come through via retry
            # Scott escalates for human review rather than auto-cancelling
            delivery.escalate(
                f"Payment pending after 90s — manual review required. "
                f"PaySync ref: {result.reference}"
            )

        logger.info(f"\nFinal delivery state: {delivery}")
        return delivery

    def check_payment_for_delivery(self, delivery_id: str) -> str:
        """
        Look up payment status for a delivery by its ID.
        Used by Scott's reconciliation jobs.

        Returns: 'success' | 'pending' | 'failed' | 'not_found'
        """
        try:
            response = self.client.session.get(
                f"{PAYSYNC_BASE_URL}/api/v1/payments/"
                f"?source_system=scott&external_reference={delivery_id}",
                timeout=10,
            )
            data = response.json()

            if not data.get('success'):
                return 'not_found'

            payments = data['data']['payments']
            if not payments:
                return 'not_found'

            # Most recent payment for this delivery
            return payments[0]['status']

        except Exception as e:
            logger.error(f"Error checking delivery payment status: {e}")
            return 'not_found'


# ── Simulation runner ──────────────────────────────────────────────────────────

def run_scott_simulation():
    """
    Runs three delivery payment scenarios.
    """
    flow = ScottPaymentFlow()

    # ── Scenario 1: Standard delivery payment ─────────────────────────────────
    print("\n" + "="*60)
    print("SCENARIO 1: Standard delivery fee collection")
    print("="*60)

    delivery_1 = DeliveryOrder(
        delivery_id=f"SCOTT_DEL_{uuid.uuid4().hex[:8].upper()}",
        rider="Brian M.",
        destination="Westlands, Nairobi",
        delivery_fee=150,
        phone="254708374149",
    )
    flow.collect_delivery_fee(delivery_1)

    # ── Scenario 2: Reconciliation check ──────────────────────────────────────
    if delivery_1.paysync_ref:
        print("\n" + "="*60)
        print("SCENARIO 2: Scott reconciliation check")
        print("="*60)

        status = flow.check_payment_for_delivery(delivery_1.delivery_id)
        logger.info(
            f"Reconciliation result | "
            f"delivery={delivery_1.delivery_id} | status={status}"
        )

    # ── Scenario 3: High-value delivery ───────────────────────────────────────
    print("\n" + "="*60)
    print("SCENARIO 3: High-value furniture delivery")
    print("="*60)

    delivery_2 = DeliveryOrder(
        delivery_id=f"SCOTT_DEL_{uuid.uuid4().hex[:8].upper()}",
        rider="Joyce K.",
        destination="Karen, Nairobi",
        delivery_fee=800,
        phone="254708374149",
    )
    flow.collect_delivery_fee(delivery_2)


if __name__ == '__main__':
    run_scott_simulation()
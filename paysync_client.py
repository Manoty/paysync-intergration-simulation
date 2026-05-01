"""
PaySync API Client
==================
Shared client library used by both Tixora and Scott.

In a real setup, this would be a pip-installable package:
    pip install paysync-client

For simulation, both scripts import from this file directly.
"""

import time
import logging
import requests
from dataclasses import dataclass
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('paysync_client')


# ── Response types ─────────────────────────────────────────────────────────────

@dataclass
class PaymentResult:
    """
    Clean result object returned to Tixora/Scott.
    They never see raw HTTP responses.
    """
    success: bool
    reference: Optional[str]
    status: str                        # pending | success | failed
    message: str
    retry_count: int = 0
    next_retry_at: Optional[str] = None
    failure_reason: Optional[str] = None
    raw: Optional[dict] = None         # Full response for debugging


class PaySyncError(Exception):
    """Raised when PaySync returns an unexpected error."""
    pass


# ── Core client ────────────────────────────────────────────────────────────────

class PaySyncClient:
    """
    HTTP client for the PaySync API.

    Usage:
        client = PaySyncClient(base_url="http://localhost:8000", source_system="tixora")
        result = client.initiate_payment(amount=500, phone="0712345678", reference="ORDER_1")

        if result.status == "success":
            confirm_order()
        elif result.status == "failed":
            cancel_order(result.failure_reason)
    """

    def __init__(self, base_url: str, source_system: str, timeout: int = 30):
        self.base_url      = base_url.rstrip('/')
        self.source_system = source_system
        self.timeout       = timeout
        self.session       = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Source-System': source_system,  # Informational header
        })
        logger.info(
            f"PaySyncClient initialised | "
            f"base_url={base_url} | source_system={source_system}"
        )

    # ── Payment initiation ─────────────────────────────────────────────────────

    def initiate_payment(
        self,
        amount: int,
        phone_number: str,
        external_reference: str,
    ) -> PaymentResult:
        """
        Initiate a payment via PaySync.

        Args:
            amount: Amount in KES (whole numbers only — M-Pesa limitation)
            phone_number: Customer phone (07XXXXXXXX or 2547XXXXXXXX)
            external_reference: Your order/delivery ID (e.g. ORDER_123)

        Returns:
            PaymentResult with reference and initial status
        """
        url = f"{self.base_url}/api/v1/payments/initiate/"
        payload = {
            "amount": amount,
            "phone_number": phone_number,
            "external_reference": external_reference,
            "source_system": self.source_system,
        }

        logger.info(
            f"Initiating payment | ref={external_reference} | "
            f"amount=KES {amount} | phone={phone_number}"
        )

        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
            data     = response.json()

            if response.status_code in (200, 201) and data.get('success'):
                payment_data = data['data']
                result = PaymentResult(
                    success=True,
                    reference=payment_data['reference'],
                    status=payment_data['status'],
                    message=data['message'],
                    retry_count=payment_data.get('retry_count', 0),
                    raw=data,
                )
                logger.info(
                    f"Payment initiated | "
                    f"paysync_ref={result.reference} | "
                    f"status={result.status}"
                )
                return result

            else:
                # Validation error or PaySync rejected the request
                errors  = data.get('errors', {})
                message = data.get('message', 'Unknown error from PaySync')
                logger.error(
                    f"Payment initiation rejected | "
                    f"status_code={response.status_code} | "
                    f"message={message} | errors={errors}"
                )
                return PaymentResult(
                    success=False,
                    reference=None,
                    status='failed',
                    message=message,
                    failure_reason=str(errors or message),
                    raw=data,
                )

        except requests.exceptions.Timeout:
            logger.error(f"Timeout initiating payment for {external_reference}")
            raise PaySyncError("PaySync request timed out. Try again.")

        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to PaySync — is the server running?")
            raise PaySyncError("Cannot connect to PaySync.")

    # ── Status polling ─────────────────────────────────────────────────────────

    def get_payment_status(self, reference: str) -> PaymentResult:
        """
        Fetch current status of a payment.

        Args:
            reference: PaySync internal UUID returned from initiate_payment

        Returns:
            PaymentResult with current status
        """
        url = f"{self.base_url}/api/v1/payments/{reference}/status/"

        try:
            response = self.session.get(url, timeout=self.timeout)
            data     = response.json()

            if response.status_code == 200 and data.get('success'):
                d = data['data']
                return PaymentResult(
                    success=True,
                    reference=d['reference'],
                    status=d['status'],
                    message=d.get('message', ''),
                    retry_count=d.get('retry_count', 0),
                    next_retry_at=d.get('next_retry_at'),
                    failure_reason=d.get('failure_reason'),
                    raw=data,
                )

            elif response.status_code == 404:
                raise PaySyncError(f"Payment {reference} not found.")

            else:
                raise PaySyncError(
                    f"Unexpected response from PaySync: "
                    f"{response.status_code} — {data.get('message')}"
                )

        except requests.exceptions.Timeout:
            raise PaySyncError("PaySync status check timed out.")

        except requests.exceptions.ConnectionError:
            raise PaySyncError("Cannot connect to PaySync.")

    # ── Polling loop ───────────────────────────────────────────────────────────

    def poll_until_complete(
        self,
        reference: str,
        max_wait_seconds: int = 120,
        poll_interval_seconds: int = 5,
    ) -> PaymentResult:
        """
        Poll payment status until it reaches a terminal state.

        Terminal states: success | failed (with no retry scheduled)

        Args:
            reference: PaySync payment UUID
            max_wait_seconds: Give up after this many seconds
            poll_interval_seconds: Seconds between each status check

        Returns:
            Final PaymentResult when terminal state reached or timeout
        """
        start_time    = time.time()
        poll_count    = 0
        last_status   = None

        logger.info(
            f"Starting status poll | ref={reference} | "
            f"max_wait={max_wait_seconds}s | interval={poll_interval_seconds}s"
        )

        while True:
            elapsed = time.time() - start_time

            if elapsed > max_wait_seconds:
                logger.warning(
                    f"Poll timeout after {int(elapsed)}s | ref={reference} | "
                    f"polls={poll_count} | last_status={last_status}"
                )
                return PaymentResult(
                    success=False,
                    reference=reference,
                    status='pending',
                    message=(
                        f"Payment still pending after {max_wait_seconds}s. "
                        f"Check again later."
                    ),
                )

            poll_count += 1
            result = self.get_payment_status(reference)
            last_status = result.status

            logger.info(
                f"Poll #{poll_count} | ref={reference} | "
                f"status={result.status} | retry_count={result.retry_count} | "
                f"elapsed={int(elapsed)}s"
            )

            # ── Terminal: success ─────────────────────────────────────────────
            if result.status == 'success':
                logger.info(
                    f"Payment confirmed SUCCESS | ref={reference} | "
                    f"polls={poll_count} | elapsed={int(elapsed)}s"
                )
                return result

            # ── Terminal: failed with no retry coming ─────────────────────────
            if result.status == 'failed' and not result.next_retry_at:
                logger.warning(
                    f"Payment permanently FAILED | ref={reference} | "
                    f"reason={result.failure_reason} | "
                    f"polls={poll_count} | elapsed={int(elapsed)}s"
                )
                return result

            # ── Still pending — log why and wait ──────────────────────────────
            if result.next_retry_at:
                logger.info(
                    f"Retry scheduled for {result.next_retry_at} — "
                    f"continuing to poll..."
                )

            time.sleep(poll_interval_seconds)
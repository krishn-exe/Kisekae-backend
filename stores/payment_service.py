import calendar
from datetime import datetime
from decimal import Decimal
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Case, F, Q, Sum, When
from django.utils import timezone

from orders.models import Order, OrderStatus
from .models import (
    AccountType,
    SettlementStatus,
    SettlementType,
    Store,
    StoreBankAccount,
    StoreMembership,
    StorePaymentSettings,
    StoreRole,
    StoreSettlement,
)
from .validators import (
    account_holder_validator,
    bank_account_validator,
    ifsc_validator,
    settlement_day_validator,
)

logger = logging.getLogger(__name__)

PLATFORM_CUT_PERCENTAGE = Decimal("0.15")  # 15% platform cut


class StorePaymentService:
    """
    Core service handling store financials, payments dashboard aggregations,
    bank account management, settlement configuration, and payouts.
    """

    @classmethod
    def get_monthly_summary(cls, store, year=None, month=None):
        """
        Calculates monthly financial breakdown for a store:
        - total_revenue: Gross product value sold
        - platforms_cut: Base 15% platform cut
        - coupon_discount_absorbed: Platform coupons absorbed out of the platform cut
        - net_platform_cut: Platform cut after absorbing coupon costs
        - advertising_cost: Current advertising cost (from store advertising budget)
        - final_gross_profit: Net earnings due to the store
        - available_unsettled_balance: Lifetime unsettled earnings available for payout
        """
        now = timezone.now()
        target_year = int(year) if year else now.year
        target_month = int(month) if month else now.month

        if target_month < 1 or target_month > 12:
            raise ValidationError("Month must be between 1 and 12.")

        days_in_month = calendar.monthrange(target_year, target_month)[1]
        current_tz = timezone.get_current_timezone()
        start_date = timezone.make_aware(
            datetime(target_year, target_month, 1, 0, 0, 0),
            current_tz,
        )
        end_date = timezone.make_aware(
            datetime(target_year, target_month, days_in_month, 23, 59, 59, 999999),
            current_tz,
        )

        eligible_statuses = [
            OrderStatus.CONFIRMED,
            OrderStatus.SHIPPED,
            OrderStatus.OUT_FOR_DELIVERY,
            OrderStatus.DELIVERED,
        ]

        # Monthly orders aggregation
        monthly_orders = Order.objects.filter(
            store=store,
            status__in=eligible_statuses,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

        orders_count = monthly_orders.count()

        monthly_agg = monthly_orders.aggregate(
            revenue=Sum(
                Case(
                    When(subtotal_amount__gt=Decimal("0.00"), then=F("subtotal_amount")),
                    default=F("total_amount") + F("coupon_discount") + F("wallet_amount_used"),
                    output_field=models.DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            coupon_discounts=Sum("coupon_discount"),
        )

        total_revenue = monthly_agg["revenue"] or Decimal("0.00")
        total_revenue = total_revenue.quantize(Decimal("0.01"))

        coupon_discounts = monthly_agg["coupon_discounts"] or Decimal("0.00")
        coupon_discounts = coupon_discounts.quantize(Decimal("0.01"))

        # 15% platform cut
        gross_platform_cut = (total_revenue * PLATFORM_CUT_PERCENTAGE).quantize(Decimal("0.01"))

        # Platform absorbs coupon costs from its 15% cut
        # Net platform cut = max(0, gross_cut - coupon_discount)
        net_platform_cut = max(Decimal("0.00"), gross_platform_cut - coupon_discounts).quantize(Decimal("0.01"))
        absorbed_coupon_cost = min(gross_platform_cut, coupon_discounts).quantize(Decimal("0.01"))

        # Store payment settings & advertising spend
        payment_settings, _ = StorePaymentSettings.objects.get_or_create(store=store)
        advertising_budget = payment_settings.monthly_advertising_budget.quantize(Decimal("0.01"))
        from .models import AdEvent
        month_ad_events = AdEvent.objects.filter(
            store=store,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )
        if month_ad_events.exists():
            ad_spend_agg = month_ad_events.aggregate(s=Sum("cost"))["s"] or Decimal("0.00")
            advertising_cost = min(advertising_budget, ad_spend_agg).quantize(Decimal("0.01"))
        else:
            advertising_cost = advertising_budget

        # Final Gross Profit for the month
        final_gross_profit = (total_revenue - net_platform_cut - advertising_cost).quantize(Decimal("0.01"))

        # Monthly settlements
        monthly_settled_agg = StoreSettlement.objects.filter(
            store=store,
            status=SettlementStatus.COMPLETED,
            processed_at__gte=start_date,
            processed_at__lte=end_date,
        ).aggregate(s=Sum("amount"))["s"]
        monthly_settled = (monthly_settled_agg or Decimal("0.00")).quantize(Decimal("0.01"))

        # Lifetime calculations for overall settlement balance
        lifetime_orders = Order.objects.filter(
            store=store,
            status__in=eligible_statuses,
        )
        lifetime_agg = lifetime_orders.aggregate(
            revenue=Sum(
                Case(
                    When(subtotal_amount__gt=Decimal("0.00"), then=F("subtotal_amount")),
                    default=F("total_amount") + F("coupon_discount") + F("wallet_amount_used"),
                    output_field=models.DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            coupon_discounts=Sum("coupon_discount"),
        )
        lifetime_revenue = (lifetime_agg["revenue"] or Decimal("0.00")).quantize(Decimal("0.01"))
        lifetime_coupons = (lifetime_agg["coupon_discounts"] or Decimal("0.00")).quantize(Decimal("0.01"))
        lifetime_gross_platform_cut = (lifetime_revenue * PLATFORM_CUT_PERCENTAGE).quantize(Decimal("0.01"))
        lifetime_net_platform_cut = max(Decimal("0.00"), lifetime_gross_platform_cut - lifetime_coupons).quantize(Decimal("0.01"))

        # Lifetime settlements completed
        total_settled_lifetime_agg = StoreSettlement.objects.filter(
            store=store,
            status=SettlementStatus.COMPLETED,
        ).aggregate(s=Sum("amount"))["s"]
        total_settled_lifetime = (total_settled_lifetime_agg or Decimal("0.00")).quantize(Decimal("0.01"))

        # Lifetime advertising cost
        lifetime_ad_events = AdEvent.objects.filter(store=store)
        if lifetime_ad_events.exists():
            lifetime_ad_spend_agg = lifetime_ad_events.aggregate(s=Sum("cost"))["s"] or Decimal("0.00")
            lifetime_advertising_cost = lifetime_ad_spend_agg.quantize(Decimal("0.01"))
        else:
            lifetime_advertising_cost = advertising_cost

        lifetime_gross_profit = max(
            Decimal("0.00"),
            lifetime_revenue - lifetime_net_platform_cut - lifetime_advertising_cost,
        ).quantize(Decimal("0.01"))

        available_unsettled_balance = max(
            Decimal("0.00"),
            lifetime_gross_profit - total_settled_lifetime,
        ).quantize(Decimal("0.01"))

        # Linked bank details & next settlement date
        bank_account = StoreBankAccount.objects.filter(store=store).first()
        next_settlement_date = cls.calculate_next_settlement_date(payment_settings.settlement_day)

        return {
            "period": {
                "year": target_year,
                "month": target_month,
                "days_in_month": days_in_month,
            },
            "currency": "INR",
            "summary": {
                "total_revenue": str(total_revenue),
                "platform_cut_percentage": 15.0,
                "gross_platform_cut": str(gross_platform_cut),
                "coupon_discount_absorbed": str(coupon_discounts),
                "net_platform_cut": str(net_platform_cut),
                "advertising_cost": str(advertising_cost),
                "final_gross_profit": str(final_gross_profit),
                "orders_count": orders_count,
            },
            "advertising": {
                "monthly_budget": str(advertising_budget),
                "current_ad_spend": str(advertising_cost),
                "remaining_budget": str(max(Decimal("0.00"), advertising_budget - advertising_cost)),
            },
            "settlement_overview": {
                "available_unsettled_balance": str(available_unsettled_balance),
                "total_settled_this_month": str(monthly_settled),
                "total_settled_lifetime": str(total_settled_lifetime),
                "lifetime_gross_profit": str(lifetime_gross_profit),
                "auto_settlement_enabled": payment_settings.auto_settlement_enabled,
                "settlement_day": payment_settings.settlement_day,
                "next_settlement_date": next_settlement_date.strftime("%Y-%m-%d") if next_settlement_date else None,
            },
            "bank_account": {
                "is_linked": bank_account is not None,
                "account_holder_name": bank_account.account_holder_name if bank_account else None,
                "account_number_masked": bank_account.account_number_masked if bank_account else None,
                "ifsc_code": bank_account.ifsc_code if bank_account else None,
                "bank_name": bank_account.bank_name if bank_account else None,
                "branch_name": bank_account.branch_name if bank_account else None,
                "account_type": bank_account.account_type if bank_account else None,
                "added_at": bank_account.created_at.isoformat() if bank_account else None,
            },
        }

    @staticmethod
    def calculate_next_settlement_date(settlement_day):
        """
        Calculates the next calendar date for automatic settlement.
        Caps day to the maximum days in that month (e.g. Feb 28).
        """
        today = timezone.now().date()
        target_day = settlement_day

        # Check if settlement day is later this month
        max_days_current = calendar.monthrange(today.year, today.month)[1]
        effective_day_current = min(target_day, max_days_current)

        if today.day < effective_day_current:
            return today.replace(day=effective_day_current)

        # Otherwise next month
        if today.month == 12:
            next_year = today.year + 1
            next_month = 1
        else:
            next_year = today.year
            next_month = today.month + 1

        max_days_next = calendar.monthrange(next_year, next_month)[1]
        effective_day_next = min(target_day, max_days_next)
        return today.replace(year=next_year, month=next_month, day=effective_day_next)

    @classmethod
    @transaction.atomic
    def set_bank_account(cls, store, user, data):
        """
        Only the store owner can create or modify bank account details.
        Applies proper regex validations.
        """
        # Permission check: OWNER ONLY
        membership = StoreMembership.objects.filter(store=store, user=user).first()
        if not membership or membership.role != StoreRole.OWNER:
            raise PermissionDenied("Only the store owner can modify bank account details.")

        account_holder_name = str(data.get("account_holder_name", "")).strip()
        account_number = str(data.get("account_number", "")).strip()
        ifsc_code = str(data.get("ifsc_code", "")).strip().upper()
        bank_name = str(data.get("bank_name", "")).strip()
        branch_name = str(data.get("branch_name", "")).strip()
        account_type = data.get("account_type", AccountType.CURRENT)

        # Validate with regex
        account_holder_validator(account_holder_name)
        bank_account_validator(account_number)
        ifsc_validator(ifsc_code)

        bank_account = StoreBankAccount.objects.filter(store=store).first()
        if bank_account:
            bank_account.account_holder_name = account_holder_name
            bank_account.account_number = account_number
            bank_account.ifsc_code = ifsc_code
            bank_account.bank_name = bank_name
            bank_account.branch_name = branch_name
            bank_account.account_type = account_type
            bank_account.save()
        else:
            bank_account = StoreBankAccount.objects.create(
                store=store,
                account_holder_name=account_holder_name,
                account_number=account_number,
                ifsc_code=ifsc_code,
                bank_name=bank_name,
                branch_name=branch_name,
                account_type=account_type,
            )

        logger.info("Store %s bank account updated by owner %s", store.id, user.id)
        return bank_account

    @classmethod
    @transaction.atomic
    def update_payment_settings(cls, store, user, data):
        """
        Store users (members) can modify settlement date, auto-settlement,
        and monthly advertising budget.
        """
        membership = StoreMembership.objects.filter(store=store, user=user).first()
        if not membership:
            raise PermissionDenied("You are not a member of this store.")

        settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)

        if "settlement_day" in data:
            raw_day = str(data["settlement_day"]).strip()
            settlement_day_validator(raw_day)
            day_val = int(raw_day)
            if day_val < 1 or day_val > 31:
                raise ValidationError({"settlement_day": "Settlement day must be between 1 and 31."})
            settings_obj.settlement_day = day_val

        if "auto_settlement_enabled" in data:
            settings_obj.auto_settlement_enabled = bool(data["auto_settlement_enabled"])

        if "monthly_advertising_budget" in data:
            try:
                budget = Decimal(str(data["monthly_advertising_budget"]))
            except Exception:
                raise ValidationError({"monthly_advertising_budget": "Invalid monetary amount."})
            if budget < Decimal("0.00"):
                raise ValidationError({"monthly_advertising_budget": "Advertising budget cannot be negative."})
            settings_obj.monthly_advertising_budget = budget.quantize(Decimal("0.01"))

        settings_obj.save()
        logger.info("Store %s payment settings updated by user %s", store.id, user.id)
        return settings_obj

    @classmethod
    @transaction.atomic
    def settle_directly(cls, store, user, amount=None, notes=""):
        """
        Allows store users to settle available unsettled balance directly into the linked account.
        """
        membership = StoreMembership.objects.filter(store=store, user=user).first()
        if not membership:
            raise PermissionDenied("You are not a member of this store.")

        bank_account = StoreBankAccount.objects.filter(store=store).first()
        if not bank_account:
            raise ValidationError(
                {"bank_account": "No linked bank account found. Store owner must add bank account details before settling."},
                code="NO_BANK_ACCOUNT",
            )

        # Calculate available unsettled balance
        summary = cls.get_monthly_summary(store)
        available_balance = Decimal(summary["settlement_overview"]["available_unsettled_balance"])

        if available_balance <= Decimal("0.00"):
            raise ValidationError(
                {"amount": "No unsettled balance available for settlement."},
                code="INSUFFICIENT_FUNDS",
            )

        if amount is not None:
            try:
                settle_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
            except Exception:
                raise ValidationError({"amount": "Invalid settlement amount format."})

            if settle_amount <= Decimal("0.00"):
                raise ValidationError({"amount": "Settlement amount must be greater than zero."})

            if settle_amount > available_balance:
                raise ValidationError(
                    {"amount": f"Requested amount (INR {settle_amount}) exceeds available balance (INR {available_balance})."},
                    code="EXCEEDS_BALANCE",
                )
        else:
            settle_amount = available_balance

        settlement = StoreSettlement.objects.create(
            store=store,
            amount=settle_amount,
            status=SettlementStatus.COMPLETED,
            settlement_type=SettlementType.MANUAL,
            account_holder_name_snapshot=bank_account.account_holder_name,
            account_number_snapshot=bank_account.account_number_masked,
            ifsc_code_snapshot=bank_account.ifsc_code,
            bank_name_snapshot=bank_account.bank_name,
            notes=notes or "Direct on-demand settlement into linked bank account.",
            initiated_by=user,
            processed_at=timezone.now(),
        )

        logger.info(
            "Settlement %s of INR %s processed for store %s by user %s",
            settlement.settlement_number,
            settle_amount,
            store.id,
            user.id,
        )

        return settlement

    @classmethod
    def process_daily_automatic_settlements(cls):
        """
        Runs automatically on schedule (e.g. daily via Celery).
        Processes payout for stores whose settlement_day matches today.
        """
        today = timezone.now().date()
        today_day = today.day
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        is_month_end = today_day == days_in_month

        # Find stores scheduled for today
        if is_month_end:
            # Handles day 31 (or 29-31) in shorter months
            qualifying_settings = StorePaymentSettings.objects.filter(
                auto_settlement_enabled=True,
                settlement_day__gte=today_day,
            ).select_related("store")
        else:
            qualifying_settings = StorePaymentSettings.objects.filter(
                auto_settlement_enabled=True,
                settlement_day=today_day,
            ).select_related("store")

        processed_count = 0
        total_payout = Decimal("0.00")

        for settings_obj in qualifying_settings:
            store = settings_obj.store
            bank_account = StoreBankAccount.objects.filter(store=store).first()
            if not bank_account:
                continue

            try:
                with transaction.atomic():
                    summary = cls.get_monthly_summary(store)
                    available_balance = Decimal(summary["settlement_overview"]["available_unsettled_balance"])

                    if available_balance > Decimal("0.00"):
                        settlement = StoreSettlement.objects.create(
                            store=store,
                            amount=available_balance,
                            status=SettlementStatus.COMPLETED,
                            settlement_type=SettlementType.AUTOMATIC,
                            account_holder_name_snapshot=bank_account.account_holder_name,
                            account_number_snapshot=bank_account.account_number_masked,
                            ifsc_code_snapshot=bank_account.ifsc_code,
                            bank_name_snapshot=bank_account.bank_name,
                            notes=f"Automated monthly settlement for day {settings_obj.settlement_day}.",
                            processed_at=timezone.now(),
                        )
                        processed_count += 1
                        total_payout += available_balance
                        logger.info(
                            "Auto-settlement %s for store %s (INR %s) succeeded.",
                            settlement.settlement_number,
                            store.id,
                            available_balance,
                        )
            except Exception as e:
                logger.error("Auto-settlement failed for store %s: %s", store.id, str(e), exc_info=True)

        return {"processed_count": processed_count, "total_payout": str(total_payout)}

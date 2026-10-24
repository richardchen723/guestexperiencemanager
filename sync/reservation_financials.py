"""Conservative reservation finance mapping, shared by sync and bounded repair.

The separate financeField list is canonical. Never combine it with reservationFees.
An absent/NULL/invalid generic amount is unknown, not zero or an instruction to
erase a stored value. Component tax is usable only when the complete included
component total reconciles to the same USD reservation total.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Optional

MONEY_FIELDS = {
    'total_price': 'totalPrice', 'tax_amount': 'taxAmount',
    'cleaning_fee': 'cleaningFee', 'security_deposit_fee': 'securityDepositFee',
}
CENT = Decimal('0.01')
SUPPORTED_COMPONENT_CHANNELS = {'bookingcom','bookingengine','direct','google','partner','homeaway'}


def amount(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and abs(result) <= Decimal('1e12') else None


def presence(raw: Mapping, key: str) -> str:
    if key not in raw:
        return 'absent'
    if raw[key] is None:
        return 'null'
    return 'valid' if amount(raw[key]) is not None else 'invalid'


@dataclass
class FinancialMapping:
    values: dict = field(default_factory=dict)
    states: dict = field(default_factory=dict)
    tax_basis: Optional[str] = None
    component_total: Optional[Decimal] = None
    reason: Optional[str] = None


def component_tax(raw: Mapping, components: Any) -> FinancialMapping:
    result = FinancialMapping()
    if raw.get('channelName') not in SUPPORTED_COMPONENT_CHANNELS:
        result.reason = 'unvalidated_channel_component_semantics'
        return result
    total = amount(raw.get('totalPrice'))
    if raw.get('currency') != 'USD' or total is None or total < 0:
        result.reason = 'unverified_usd_total'
        return result
    if not isinstance(components, list) or not components:
        result.reason = 'components_unavailable'
        return result
    included, taxes, cleaning, identities = [], [], [], set()
    deleted_tax = False
    for item in components:
        if not isinstance(item, dict):
            result.reason = 'invalid_component'
            return result
        deleted = item.get('isDeleted')
        included_flag = item.get('isIncludedInTotalPrice')
        if type(deleted) is not int or deleted not in (0, 1):
            result.reason = 'unknown_deletion_flag'
            return result
        if deleted:
            deleted_tax = deleted_tax or item.get('type') == 'tax'
            continue
        if item.get('currency') not in (None, 'USD'):
            result.reason = 'conflicting_component_currency'
            return result
        identity = item.get('id')
        if type(identity) is not int or identity <= 0 or identity in identities:
            result.reason = 'missing_or_duplicate_component_id'
            return result
        identities.add(identity)
        if type(included_flag) is not int or included_flag not in (0, 1):
            result.reason = 'unknown_inclusion_flag'
            return result
        # Active tax outside total requires different semantics; do not subtract it.
        if item.get('type') == 'tax' and not included_flag:
            result.reason = 'tax_not_included_in_total'
            return result
        if not included_flag:
            continue
        if item.get('name') in {'totalPriceFromChannel', 'totalPaid', 'totalPrice'}:
            result.reason = 'included_aggregate_component'
            return result
        value = amount(item.get('total'))
        if value is None:
            result.reason = 'invalid_component_total'
            return result
        # Included aggregates and unknown component types can double count totals.
        if item.get('type') not in {'accommodation', 'price', 'fee', 'tax', 'discount', 'commissions', 'other'}:
            result.reason = 'unsupported_included_component_type'
            return result
        included.append(value)
        if item.get('name') == 'cleaningFee':
            if item.get('type') != 'fee':
                result.reason = 'unsupported_cleaning_representation'
                return result
            cleaning.append(value)
        if item.get('type') == 'tax':
            if value < 0:
                result.reason = 'negative_tax_requires_review'
                return result
            taxes.append(value)
    result.component_total = sum(included, Decimal(0))
    if not taxes:
        result.reason = 'deleted_tax_only_unknown' if deleted_tax else 'no_tax_component_unknown'
        return result
    if abs(result.component_total - total) > CENT:
        result.reason = 'component_total_mismatch'
        return result
    generic_cleaning = amount(raw.get('cleaningFee'))
    if generic_cleaning is None or generic_cleaning < 0:
        result.reason = 'unverified_generic_cleaning'
        return result
    if abs(sum(cleaning, Decimal(0)) - generic_cleaning) > CENT:
        result.reason = 'cleaning_component_mismatch'
        return result
    tax = sum(taxes, Decimal(0))
    if tax > total:
        result.reason = 'tax_exceeds_total'
        return result
    result.values['tax_amount'] = float(tax)
    result.tax_basis = 'reconciled_financeField_total'
    return result


def complete_components(resource):
    if not isinstance(resource,list) or not resource:
        return False
    for item in resource:
        if not isinstance(item,dict) or not {'id','type','total','isDeleted','isIncludedInTotalPrice'}<=item.keys():
            return False
        if type(item['isDeleted']) is not int or item['isDeleted'] not in (0,1):
            return False
        if item['isDeleted']:
            continue
        if type(item['id']) is not int or type(item['isIncludedInTotalPrice']) is not int or item['isIncludedInTotalPrice'] not in (0,1):
            return False
        if item['isIncludedInTotalPrice'] and amount(item['total']) is None:
            return False
    return True


def needs_financial_hydration(raw, existing_tax=None):
    return (existing_tax is None and presence(raw,'taxAmount') in {'absent','null'}
            and raw.get('channelName') in SUPPORTED_COMPONENT_CHANNELS
            and raw.get('currency')=='USD' and amount(raw.get('totalPrice')) is not None
            and amount(raw.get('cleaningFee')) is not None
            and not complete_components(raw.get('financeField')))


def hydrate_financials(client, raw, existing_tax=None):
    """Hydrate one exact reservation; never infer identity from a cursor.

    Only tax/components are taken from the detail snapshot. Existing list
    amounts must agree with that snapshot; known stored tax is never hydrated.
    """
    mapped=map_financials(raw,existing_tax=existing_tax)
    if not needs_financial_hydration(raw,existing_tax):
        return mapped
    def unknown(reason):
        mapped.values.pop('tax_amount',None)
        mapped.tax_basis=None;mapped.reason=reason
        mapped.states['tax_hydration']=reason
        return mapped
    try:
        detail=client.get_reservation_detail(raw['id'])
        if not isinstance(detail,dict) or detail.get('id')!=raw['id'] or detail.get('listingMapId')!=raw.get('listingMapId'):
            return unknown('hydration_failed_or_wrong_identity')
        if detail.get('updatedOn')!=raw.get('updatedOn') or detail.get('currency')!=raw.get('currency'):
            return unknown('hydration_snapshot_mismatch')
        for key in ('totalPrice','cleaningFee','securityDepositFee'):
            if key in raw and amount(raw[key])!=amount(detail.get(key)):
                return unknown('hydration_snapshot_mismatch')
        hydrated=dict(raw)
        detail_tax_state=presence(detail,'taxAmount')
        if detail_tax_state=='invalid':
            mapped.states['tax_amount']='invalid'
            return unknown('invalid_generic_tax')
        if detail_tax_state=='valid':
            hydrated['taxAmount']=detail['taxAmount']
        else:
            if detail_tax_state=='absent':
                hydrated.pop('taxAmount',None)
            else:
                hydrated['taxAmount']=None
            components=detail.get('financeField')
            if not complete_components(components):
                components=client.get_reservation_finance_fields(raw['id'])
            if not isinstance(components,list):
                return unknown('hydration_components_unavailable')
            hydrated['financeField']=components
        mapped=map_financials(hydrated,existing_tax=existing_tax)
        mapped.states['tax_hydration']='exact_id_verified'
        return mapped
    except Exception:
        return unknown('hydration_failed')


def map_financials(raw: Mapping, *, components: Any = None, existing_tax: Any = None) -> FinancialMapping:
    """Return only validated writes; states retain absent versus explicit NULL.

    No defaults are emitted, so applying this to an existing row preserves its
    known fields on absent/NULL/invalid inputs. New rows retain database NULLs.
    """
    result = FinancialMapping()
    for column, key in MONEY_FIELDS.items():
        state = presence(raw, key)
        result.states[column] = state
        if state == 'valid':
            result.values[column] = float(amount(raw[key]))
    currency = raw.get('currency')
    result.states['currency'] = 'absent' if 'currency' not in raw else 'null' if currency is None else 'valid' if isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency) else 'invalid'
    if result.states['currency'] == 'valid':
        result.values['currency'] = currency
    if result.states['tax_amount'] == 'valid':
        result.tax_basis = 'generic_taxAmount'
        return result
    if result.states['tax_amount'] == 'invalid':
        result.reason = 'invalid_generic_tax'
        return result
    canonical = components if components is not None else raw.get('financeField')
    resolved = component_tax(raw, canonical)
    result.component_total, result.reason = resolved.component_total, resolved.reason
    if resolved.tax_basis:
        if existing_tax is not None:
            stored = amount(existing_tax)
            candidate = amount(resolved.values['tax_amount'])
            if stored is None:
                result.reason = 'invalid_stored_tax_requires_review'
                result.states['tax_amount'] += '_invalid_stored_preserved'
            elif stored != candidate:
                result.reason = 'component_tax_conflicts_with_stored'
                result.states['tax_amount'] += '_component_conflict_preserved'
            else:
                result.reason = None
                result.tax_basis = 'stored_tax_preserved'
                result.states['tax_amount'] += '_components_match_stored'
            return result
        result.values.update(resolved.values)
        result.tax_basis = resolved.tax_basis
        result.states['tax_amount'] += '_resolved_from_components'
    return result

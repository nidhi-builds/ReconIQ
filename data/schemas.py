from datetime import date
from pydantic import BaseModel


class LedgerEntry(BaseModel):
    order_id: str
    ref_id: str | None
    amount: float
    currency: str
    payment_method: str
    order_date: date
    customer_id: str


class SettlementEntry(BaseModel):
    settlement_id: str
    ref_id: str | None
    gross_amount: float
    fee: float
    gst_on_fee: float
    net_amount: float
    payment_method: str
    settlement_date: date
    tds_deducted: float
    tds_section: str
    challan_number: str | None


class BankEntry(BaseModel):
    bank_txn_id: str
    ref_id: str | None
    amount: float
    value_date: date
    narration: str


class GroundTruthEntry(BaseModel):
    case_id: str
    split: str
    case_type: str
    ledger_ids: list[str]
    settlement_ids: list[str]
    bank_txn_ids: list[str]
    is_transaction: bool | None
    true_match_group: str | None
    true_exception_reason: str | None
    true_tax_mismatch: str | None = None
    confusable_with: str | None = None
    refund_of: str | None = None


class ReconciliationResult(BaseModel):
    record_ids: list[str]
    matched: bool
    confidence: float
    method: str
    exception_reason: str | None
    reasoning: str | None


class Tax26ASEntry(BaseModel):
    case_id: str
    ref_id: str | None
    tds_expected: float
    tds_section_expected: str
    challan_number: str | None


class DatasetPartition(BaseModel):
    ledger: list[LedgerEntry]
    settlements: list[SettlementEntry]
    bank: list[BankEntry]
    tax_26as: list[Tax26ASEntry]
    ground_truth: list[GroundTruthEntry]


class SyntheticDataset(BaseModel):
    design: DatasetPartition
    holdout: DatasetPartition

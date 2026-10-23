-- Additive operational KPI snapshot storage only. Existing brain schema required.
BEGIN;

CREATE TABLE IF NOT EXISTS brain.operational_kpi_snapshot_state (
	state_key VARCHAR NOT NULL,
	contract_version VARCHAR NOT NULL,
	generation INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	lease_token VARCHAR,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	scheduled_for_at TIMESTAMP WITH TIME ZONE,
	last_attempt_at TIMESTAMP WITH TIME ZONE,
	last_attempt_finished_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR,
	last_good_snapshot_id VARCHAR,
	next_due_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (state_key)
)

;

CREATE TABLE IF NOT EXISTS brain.operational_kpi_snapshots (
	snapshot_id VARCHAR NOT NULL,
	contract_version VARCHAR NOT NULL,
	scheduled_for_at TIMESTAMP WITH TIME ZONE NOT NULL,
	window_as_of TIMESTAMP WITH TIME ZONE NOT NULL,
	calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	source_as_of JSONB NOT NULL,
	payload JSONB NOT NULL,
	PRIMARY KEY (snapshot_id),
	CONSTRAINT uq_operational_kpi_slot UNIQUE (contract_version, scheduled_for_at)
)

;
COMMIT;

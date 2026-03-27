-- Add unique constraint to cost_tracking_log so ON CONFLICT (tenant_id, cycle_date, activity_name)
-- is valid and prevents duplicate entries per cycle
ALTER TABLE cost_tracking_log
    ADD CONSTRAINT uq_cost_tracking_log_tenant_date_activity
    UNIQUE (tenant_id, cycle_date, activity_name);

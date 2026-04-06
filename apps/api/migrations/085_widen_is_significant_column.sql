-- Widen is_significant column to accommodate 'insufficient_data' value (18 chars > old limit 10)
ALTER TABLE learning_experiments ALTER COLUMN is_significant TYPE VARCHAR(20);

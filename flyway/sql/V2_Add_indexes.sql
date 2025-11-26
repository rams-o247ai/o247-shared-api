CREATE TYPE status_type AS ENUM ('active', 'pending', 'processing');

ALTER TABLE app.subscription_line_items
ALTER COLUMN status DROP DEFAULT;

ALTER TABLE app.subscription_line_items
ALTER COLUMN status TYPE status_type
USING (
    CASE
        WHEN status = true THEN 'active'::status_type
        WHEN status = false THEN 'pending'::status_type
        ELSE 'pending'::status_type     
    END
);

ALTER TABLE app.subscription_line_items
ALTER COLUMN product_id DROP NOT NULL;

ALTER TABLE app.subscription_line_items
ADD COLUMN inbound_phone_number_id TEXT,
ADD COLUMN outbound_phone_number_id TEXT;
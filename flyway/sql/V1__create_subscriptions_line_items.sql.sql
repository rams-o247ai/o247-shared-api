-- Keep an app schema (optional)
CREATE SCHEMA IF NOT EXISTS app;

-- One row per subscription
CREATE TABLE IF NOT EXISTS app.subscriptions (
  id                      BIGSERIAL PRIMARY KEY,
  subscription_id         BIGINT UNIQUE NOT NULL,   -- maps to "subscriptionID"
  company                 TEXT NOT NULL,            -- "company"
  administrative_contact  TEXT,                     -- "administrativeContact"
  status                  TEXT NOT NULL,            -- plain TEXT (e.g., 'active','unpaid','canceled')
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  contact_id              BIGINT        NOT NULL,
  is_default			        boolean
);

-- Many line items per subscription
CREATE TABLE IF NOT EXISTS app.subscription_line_items (
  id                 BIGSERIAL PRIMARY KEY,
  subscription_id    BIGINT NOT NULL REFERENCES app.subscriptions(subscription_id) ON DELETE CASCADE,
  line_item_id       BIGINT       NOT NULL,        -- "lineItemId"
  product_id         BIGINT       NOT NULL,        -- "productId"
  product_name       TEXT         NOT NULL,        -- "productName"
  image_url          TEXT,
  description        TEXT,
  agent_id           TEXT,
  price              NUMERIC(12,2),                -- "price" comes as string; cast before insert if needed
  billing_frequency  TEXT,                         -- e.g., 'weekly', 'monthly'
  assistant_role     TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- map API createdDate → here
  contact_id         BIGINT        NOT NULL,
  fields             JSONB,
  status             BOOLEAN      NOT NULL DEFAULT FALSE,
  UNIQUE (subscription_id, line_item_id),
  business_id        VARCHAR,
  business_name      VARCHAR
);

-- -- Helpful indexes
-- CREATE INDEX IF NOT EXISTS subscriptions_status_idx ON app.subscriptions (status);
-- CREATE INDEX IF NOT EXISTS subscriptions_company_idx ON app.subscriptions (company);
-- CREATE INDEX IF NOT EXISTS li_subid_idx ON app.subscription_line_items (subscription_id);
-- CREATE INDEX IF NOT EXISTS li_product_idx ON app.subscription_line_items (product_id);

--subscription_id, line_item_id
CREATE SEQUENCE IF NOT EXISTS app.subscription_id_seq
  START WITH 100000000000 INCREMENT BY 1 MINVALUE 100000000000;
CREATE SEQUENCE IF NOT EXISTS app.line_item_id_seq
  START WITH 10000000000 INCREMENT BY 1 MINVALUE 10000000000;

  --ids
CREATE SEQUENCE IF NOT EXISTS app.subscriptions_id_seq
  START WITH 1 INCREMENT BY 1 MINVALUE 1;
CREATE SEQUENCE IF NOT EXISTS app.subscription_line_items_id
  START WITH 10000000000 INCREMENT BY 1 MINVALUE 10000000000;


ALTER TABLE app.subscriptions
  ALTER COLUMN id SET DEFAULT nextval('app.subscriptions_id_seq');

ALTER TABLE app.subscription_line_items
  ALTER COLUMN id SET DEFAULT nextval('app.subscription_line_items_id');

ALTER TABLE app.subscriptions
  ALTER COLUMN subscription_id SET DEFAULT nextval('app.subscription_id_seq');

ALTER TABLE app.subscription_line_items
  ALTER COLUMN line_item_id SET DEFAULT nextval('app.line_item_id_seq');

-- Ignis Quant System — Complete Database Initialization
-- Version: v4.0
-- Updated: 2026-02-25
--
-- This file creates ALL tables needed for the full system:
--   Core trading (positions, orders, wallet_state)
--   Saga orchestration (saga_instances, saga_steps, idempotency_keys)
--   Signal tracking (signal_push_history, implementation_shortfall)
--   Telegram VIP (membership_plans, payment_addresses, payment_orders,
--                 memberships, payment_audit_logs, vip_signal_pushes)
--   Callback compensation (callback_retry_tasks)
--   Trader program (verified_uids)
--   Feedback (user_feedback)
--
-- All statements are idempotent (CREATE TABLE IF NOT EXISTS).
-- Safe to re-run on an existing database.

CREATE DATABASE IF NOT EXISTS crypto_signals
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE crypto_signals;

-- ============================================
-- 1. positions (trading position records)
-- Single source of truth for Swing strategy
-- ============================================
CREATE TABLE IF NOT EXISTS positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL COMMENT 'LONG/SHORT',
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    strategy_name VARCHAR(50),

    -- Signal/order references
    entry_signal_id BIGINT,
    entry_order_id BIGINT,
    exit_signal_id BIGINT,
    exit_order_id BIGINT,

    -- Pending order
    pending_order_id VARCHAR(50),
    pending_limit_price DECIMAL(20,8),
    pending_created_at DATETIME,

    -- Entry info
    entry_price DECIMAL(12,2) NOT NULL,
    quantity DECIMAL(18,8) NOT NULL,

    -- Stop loss / take profit
    stop_loss DECIMAL(12,2),
    take_profit_1 DECIMAL(12,2),
    take_profit_2 DECIMAL(12,2),

    -- Trailing stop
    stop_type VARCHAR(20) DEFAULT 'FIXED' COMMENT 'FIXED/TRAILING',
    trailing_period INT,
    trailing_mult DECIMAL(4,2),
    current_stop DECIMAL(12,2),
    entry_atr DECIMAL(12,4),
    highest_since_entry DECIMAL(12,2),

    -- Stop loss order
    sl_order_id VARCHAR(50),
    sl_trigger_price DECIMAL(20,8),

    -- Partial exit
    partial_exit_1_order_id BIGINT,
    partial_exit_1_price DECIMAL(12,2),
    partial_exit_1_quantity DECIMAL(18,8),
    partial_exit_1_at DATETIME,

    -- Exit info
    exit_price DECIMAL(12,2),
    exit_reason VARCHAR(50),
    realized_pnl DECIMAL(12,2),
    realized_pnl_percent DECIMAL(6,2),
    commission_total DECIMAL(12,8),

    -- Timestamps
    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Environment flag
    testnet TINYINT(1) DEFAULT 1,

    -- Telegram message ID (for reply threading)
    telegram_message_id BIGINT,
    notes TEXT,

    INDEX idx_symbol_status (symbol, status),
    INDEX idx_status (status),
    INDEX idx_opened_at (opened_at DESC),
    INDEX idx_entry_signal (entry_signal_id),
    INDEX idx_entry_order (entry_order_id),
    INDEX idx_telegram_msg (telegram_message_id),
    INDEX idx_sl_order (sl_order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Trading position records';

-- ============================================
-- 2. orders (order records)
-- ============================================
CREATE TABLE IF NOT EXISTS orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    quantity DECIMAL(20,8),
    price DECIMAL(20,8),
    status VARCHAR(20),
    filled_qty DECIMAL(20,8),
    avg_price DECIMAL(20,8),
    position_id BIGINT,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_order_id (order_id),
    INDEX idx_symbol (symbol),
    INDEX idx_position (position_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Order records';

-- ============================================
-- 3. wallet_state (HD wallet index tracker)
-- ============================================
CREATE TABLE IF NOT EXISTS wallet_state (
    id INT AUTO_INCREMENT PRIMARY KEY,
    current_index INT DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='HD wallet derivation state';

-- Seed wallet_state
INSERT INTO wallet_state (id, current_index) VALUES (1, 0)
ON DUPLICATE KEY UPDATE id = id;

-- ============================================
-- 4. saga_instances (Saga orchestration)
-- ============================================
CREATE TABLE IF NOT EXISTS saga_instances (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    saga_id VARCHAR(36) NOT NULL UNIQUE COMMENT 'UUID identifier',
    saga_type VARCHAR(50) NOT NULL COMMENT 'payment, trading, etc.',
    status ENUM('RUNNING', 'COMPLETED', 'COMPENSATING', 'FAILED', 'COMPENSATED') DEFAULT 'RUNNING',
    current_step INT DEFAULT 0 COMMENT 'Current execution step',
    context JSON COMMENT 'Workflow context data',
    error_message TEXT,
    started_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6),
    updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    INDEX idx_saga_type_status (saga_type, status),
    INDEX idx_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Saga instances';

-- ============================================
-- 5. saga_steps (FK -> saga_instances)
-- ============================================
CREATE TABLE IF NOT EXISTS saga_steps (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    saga_id VARCHAR(36) NOT NULL COMMENT 'References saga_instances.saga_id',
    step_index INT NOT NULL COMMENT 'Step sequence number',
    step_name VARCHAR(50) NOT NULL,
    status ENUM('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'COMPENSATED') DEFAULT 'PENDING',
    result JSON COMMENT 'Step execution result',
    error_message TEXT,
    started_at DATETIME(6),
    completed_at DATETIME(6),

    UNIQUE KEY uk_saga_step (saga_id, step_index),
    INDEX idx_saga_id (saga_id),
    FOREIGN KEY (saga_id) REFERENCES saga_instances(saga_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Saga execution steps';

-- ============================================
-- 6. idempotency_keys
-- ============================================
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    operation_type VARCHAR(50) NOT NULL,
    request_hash VARCHAR(64) COMMENT 'Request parameter hash',
    response JSON COMMENT 'Cached response',
    status ENUM('PROCESSING', 'COMPLETED', 'FAILED') DEFAULT 'PROCESSING',
    created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) COMMENT 'TTL expiration',

    INDEX idx_operation_type (operation_type),
    INDEX idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Idempotency keys for deduplication';

-- ============================================
-- 7. signal_push_history
-- ============================================
CREATE TABLE IF NOT EXISTS signal_push_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    signal_id BIGINT COMMENT 'Optional reference to signals table',
    signal_hash VARCHAR(64) NOT NULL COMMENT 'Content hash for deduplication',
    signal_type VARCHAR(20) NOT NULL COMMENT 'SWING / INTRADAY',
    target_group_id BIGINT NOT NULL COMMENT 'Target Telegram group ID',
    message_content TEXT NOT NULL COMMENT 'Signal message content',
    message_id BIGINT COMMENT 'Telegram message ID',
    analysis_msg_id BIGINT COMMENT 'Telegram analysis reply message ID',
    status ENUM('SUCCESS', 'FAILED', 'SKIPPED') DEFAULT 'SUCCESS',
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_signal_hash (signal_hash),
    INDEX idx_signal_type (signal_type),
    INDEX idx_created (created_at),
    INDEX idx_group (target_group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VIP signal push history';

-- ============================================
-- 8. implementation_shortfall
-- Tracks signal price vs actual fill price gap
-- ============================================
CREATE TABLE IF NOT EXISTS implementation_shortfall (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    position_id BIGINT NOT NULL COMMENT 'References positions.id',
    symbol VARCHAR(20) NOT NULL COMMENT 'Trading pair (e.g. BTCUSDT)',
    side VARCHAR(10) NOT NULL COMMENT 'LONG/SHORT',
    signal_price DECIMAL(20,8) NOT NULL COMMENT 'Signal price (daily close)',
    fill_price DECIMAL(20,8) NOT NULL COMMENT 'Actual fill price',
    quantity DECIMAL(20,8) NOT NULL,
    shortfall_bps DECIMAL(10,4) NOT NULL COMMENT 'IS in basis points = (fill/signal - 1) * 10000',
    shortfall_usd DECIMAL(12,4) NOT NULL COMMENT 'IS in USD = (fill - signal) * quantity',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_position (position_id),
    INDEX idx_symbol_date (symbol, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Implementation shortfall tracking';

-- ============================================
-- 9. membership_plans (VIP plan configuration)
-- ============================================
CREATE TABLE IF NOT EXISTS membership_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_code VARCHAR(20) NOT NULL UNIQUE COMMENT 'Plan code: BASIC_M/BASIC_Y/PREMIUM_M/PREMIUM_Y',
    plan_name VARCHAR(50) NOT NULL,
    price_usdt DECIMAL(10,2) NOT NULL COMMENT 'Price in USDT',
    duration_days INT NOT NULL,
    level INT NOT NULL DEFAULT 1 COMMENT 'Permission level: 1=Basic, 2=Premium',
    access_groups JSON NOT NULL COMMENT 'Accessible groups: ["BASIC"] or ["PREMIUM"]',
    enabled BOOLEAN DEFAULT TRUE,
    version INT DEFAULT 1 COMMENT 'Optimistic lock version',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_enabled (enabled),
    INDEX idx_plan_code (plan_code),
    INDEX idx_level (level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VIP membership plan configuration';

-- Seed membership plans (integer pricing v3.0)
INSERT INTO membership_plans (plan_code, plan_name, price_usdt, duration_days, level, access_groups) VALUES
    ('BASIC_M', 'Ignis Basic (Monthly)', 30.00, 30, 1, '["BASIC"]'),
    ('BASIC_Y', 'Ignis Basic (Yearly)', 300.00, 365, 1, '["BASIC"]'),
    ('PREMIUM_M', 'Ignis Premium (Monthly)', 80.00, 30, 2, '["PREMIUM"]'),
    ('PREMIUM_Y', 'Ignis Premium (Yearly)', 800.00, 365, 2, '["PREMIUM"]')
ON DUPLICATE KEY UPDATE
    plan_name = VALUES(plan_name),
    price_usdt = VALUES(price_usdt),
    duration_days = VALUES(duration_days),
    level = VALUES(level),
    access_groups = VALUES(access_groups);

-- ============================================
-- 10. payment_addresses (HD wallet address pool)
-- ============================================
CREATE TABLE IF NOT EXISTS payment_addresses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    derive_index INT NOT NULL UNIQUE COMMENT 'BIP44 derivation index',
    address VARCHAR(64) NOT NULL UNIQUE COMMENT 'BSC address',
    status VARCHAR(20) DEFAULT 'AVAILABLE' COMMENT 'AVAILABLE/ASSIGNED/USED/COLLECTING',
    order_id VARCHAR(20) COMMENT 'Current order using this address',
    telegram_id BIGINT COMMENT 'Associated Telegram user ID',
    received_amount DECIMAL(18,8) COMMENT 'Received payment amount',
    received_at DATETIME,
    received_tx_hash VARCHAR(128),
    collected_tx_hash VARCHAR(128) COMMENT 'Collection transaction hash',
    collected_at DATETIME,
    assigned_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_status (status),
    INDEX idx_order_id (order_id),
    INDEX idx_telegram_id (telegram_id),
    INDEX idx_derive_index (derive_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='HD wallet payment address pool';

-- ============================================
-- 11. payment_orders (VIP payment orders)
-- ============================================
CREATE TABLE IF NOT EXISTS payment_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(20) NOT NULL UNIQUE COMMENT 'Order ID: YYYYMMDD-XXXX',
    order_signature VARCHAR(256) COMMENT 'HMAC-SHA256 signature for replay protection',
    telegram_id BIGINT NOT NULL,
    telegram_username VARCHAR(64),
    membership_type VARCHAR(20) NOT NULL COMMENT 'Plan code',
    expected_amount DECIMAL(10,2) NOT NULL COMMENT 'Expected payment amount (snapshot)',
    discount_type ENUM('ALPHA', 'TRADER', 'NONE') DEFAULT 'NONE' COMMENT 'Discount type',
    duration_days INT NOT NULL COMMENT 'Plan duration (snapshot)',
    payment_address VARCHAR(64) NOT NULL COMMENT 'BSC payment address',
    address_index INT NOT NULL,
    actual_amount DECIMAL(10,2) COMMENT 'Actual payment amount',
    tx_hash VARCHAR(128),
    from_address VARCHAR(64) COMMENT 'Sender address',
    status ENUM('PENDING', 'CONFIRMED', 'EXPIRED', 'FAILED') DEFAULT 'PENDING',
    error_message TEXT,
    expire_at DATETIME NOT NULL,
    confirmed_at DATETIME,
    version INT DEFAULT 1 COMMENT 'Optimistic lock version',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_telegram_id (telegram_id),
    INDEX idx_status (status),
    INDEX idx_payment_address (payment_address),
    INDEX idx_expire (expire_at),
    INDEX idx_created (created_at),
    INDEX idx_discount (telegram_id, discount_type, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VIP payment orders';

-- ============================================
-- 12. memberships (VIP membership records)
-- ============================================
CREATE TABLE IF NOT EXISTS memberships (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    telegram_username VARCHAR(64),
    membership_type VARCHAR(20) COMMENT 'Current plan code',
    level INT NOT NULL DEFAULT 1 COMMENT 'Permission level: 1=Basic, 2=Premium',
    start_date DATETIME,
    expire_date DATETIME,
    status ENUM('ACTIVE', 'EXPIRED', 'CANCELLED') DEFAULT 'ACTIVE',
    is_whitelist BOOLEAN DEFAULT FALSE COMMENT 'Whitelist user (no expiry)',
    activated_by_order_id VARCHAR(20),
    last_renewed_order_id VARCHAR(20),
    renewal_count INT DEFAULT 0,
    binance_uid VARCHAR(20) COMMENT 'Binance UID (Trader Program)',
    is_referral_verified TINYINT DEFAULT 0 COMMENT 'Trader Program verified',
    referral_verified_at DATETIME,
    is_admin TINYINT DEFAULT 0,
    language VARCHAR(5) DEFAULT 'en' COMMENT 'Language preference: en/zh',
    version INT DEFAULT 1 COMMENT 'Optimistic lock version',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_status (status),
    INDEX idx_expire (expire_date),
    INDEX idx_telegram (telegram_id),
    INDEX idx_level (level),
    INDEX idx_whitelist (is_whitelist),
    INDEX idx_referral (is_referral_verified)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VIP membership records';

-- ============================================
-- 13. payment_audit_logs
-- ============================================
CREATE TABLE IF NOT EXISTS payment_audit_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(20) COMMENT 'Related order ID',
    operation VARCHAR(50) NOT NULL COMMENT 'Operation: CREATE/VERIFY/CONFIRM/EXPIRE/FAIL',
    operator VARCHAR(50) NOT NULL COMMENT 'Operator: system/admin',
    old_status VARCHAR(20),
    new_status VARCHAR(20),
    details JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_order (order_id),
    INDEX idx_operation (operation),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Payment audit logs';

-- ============================================
-- 14. vip_signal_pushes
-- ============================================
CREATE TABLE IF NOT EXISTS vip_signal_pushes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    signal_id BIGINT NOT NULL,
    telegram_id BIGINT NOT NULL,
    membership_id BIGINT,
    signal_type VARCHAR(20) NOT NULL COMMENT 'SWING',
    symbol VARCHAR(20),
    membership_status_at_push VARCHAR(20),
    level_at_push INT,
    push_time DATETIME(6),
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),

    INDEX idx_signal (signal_id),
    INDEX idx_telegram (telegram_id),
    INDEX idx_push_time (push_time),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='VIP signal push records';

-- ============================================
-- 15. callback_retry_tasks (payment callback compensation)
-- ============================================
CREATE TABLE IF NOT EXISTS callback_retry_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(20) NOT NULL COMMENT 'Order ID',
    telegram_id BIGINT NOT NULL,
    plan_code VARCHAR(30) NOT NULL,
    tx_hash VARCHAR(100),
    status ENUM('PENDING', 'RETRYING', 'SUCCESS', 'FAILED') DEFAULT 'PENDING',
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    next_retry_at DATETIME,
    completed_at DATETIME,

    UNIQUE KEY uk_order_id (order_id),
    INDEX idx_status_next_retry (status, next_retry_at),
    INDEX idx_telegram_id (telegram_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Payment callback retry tasks';

-- ============================================
-- 16. verified_uids (Trader Program verified UIDs)
-- ============================================
CREATE TABLE IF NOT EXISTS verified_uids (
    uid VARCHAR(20) PRIMARY KEY COMMENT 'Binance UID',
    telegram_id BIGINT NOT NULL,
    verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    verified_by VARCHAR(50) DEFAULT 'admin' COMMENT 'Verification method: admin/system',
    notes VARCHAR(255) DEFAULT NULL,

    INDEX idx_telegram (telegram_id),
    INDEX idx_verified_at (verified_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Verified Binance UIDs for Trader Program';

-- ============================================
-- 17. user_feedback
-- ============================================
CREATE TABLE IF NOT EXISTS user_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(64),
    content TEXT NOT NULL,
    replied TINYINT(1) DEFAULT 0,
    reply_content TEXT,
    replied_at DATETIME,
    replied_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_telegram_id (telegram_id),
    INDEX idx_created_at (created_at),
    INDEX idx_replied (replied)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='User feedback records';

-- ============================================
-- Done
-- ============================================
SELECT '=== Ignis Database v4.0 initialized (17 tables) ===' AS status;

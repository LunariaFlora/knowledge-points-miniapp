-- MySQL 8.0 schema for the first release.
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE stores (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  address VARCHAR(255) NOT NULL,
  contact_phone VARCHAR(32) NOT NULL,
  latitude DECIMAL(10,7) NULL,
  longitude DECIMAL(10,7) NULL,
  business_hours VARCHAR(100) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT chk_store_status CHECK (status IN ('ACTIVE','INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  openid VARCHAR(64) NOT NULL,
  unionid VARCHAR(64) NULL,
  nickname VARCHAR(100) NULL,
  avatar_url VARCHAR(500) NULL,
  mobile VARCHAR(32) NULL,
  tags VARCHAR(200) NOT NULL DEFAULT '',
  notes VARCHAR(1000) NOT NULL DEFAULT '',
  invite_code VARCHAR(32) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  privacy_agreed_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_users_openid (openid),
  UNIQUE KEY uk_users_invite_code (invite_code),
  KEY idx_users_unionid (unionid),
  CONSTRAINT chk_user_status CHECK (status IN ('ACTIVE','DISABLED','CANCELLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE user_sessions (
  token_hash CHAR(64) PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user_session_expiry (expires_at),
  CONSTRAINT fk_user_session_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE staff_users (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  password_salt VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(100) NOT NULL,
  role VARCHAR(30) NOT NULL,
  store_id BIGINT UNSIGNED NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  last_login_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_staff_username (username),
  KEY idx_staff_store (store_id),
  CONSTRAINT fk_staff_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT chk_staff_role CHECK (role IN ('ADMIN','STORE_VERIFIER')),
  CONSTRAINT chk_staff_status CHECK (status IN ('ACTIVE','DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE staff_sessions (
  token_hash CHAR(64) PRIMARY KEY,
  staff_id BIGINT UNSIGNED NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_staff_session_expiry (expires_at),
  CONSTRAINT fk_staff_session_staff FOREIGN KEY (staff_id) REFERENCES staff_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE video_categories (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(80) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_video_category_name (name),
  CONSTRAINT chk_video_category_status CHECK (status IN ('ACTIVE','INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE videos (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  category_id BIGINT UNSIGNED NOT NULL,
  title VARCHAR(200) NOT NULL,
  summary VARCHAR(1000) NULL,
  cover_url VARCHAR(500) NOT NULL,
  video_url VARCHAR(500) NOT NULL,
  duration_seconds INT UNSIGNED NOT NULL,
  total_points INT UNSIGNED NOT NULL DEFAULT 1,
  status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
  published_at TIMESTAMP NULL,
  created_by BIGINT UNSIGNED NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_videos_category_status (category_id, status),
  CONSTRAINT fk_videos_category FOREIGN KEY (category_id) REFERENCES video_categories(id),
  CONSTRAINT fk_videos_creator FOREIGN KEY (created_by) REFERENCES staff_users(id),
  CONSTRAINT chk_video_duration CHECK (duration_seconds > 0),
  CONSTRAINT chk_video_total_points CHECK (total_points >= 0),
  CONSTRAINT chk_video_status CHECK (status IN ('DRAFT','PUBLISHED','OFFLINE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE video_watch_sessions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  session_token CHAR(36) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  video_id BIGINT UNSIGNED NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_heartbeat_at TIMESTAMP NULL,
  last_position_seconds DECIMAL(10,2) NOT NULL DEFAULT 0,
  closed_at TIMESTAMP NULL,
  abnormal_reason VARCHAR(255) NULL,
  UNIQUE KEY uk_watch_session_token (session_token),
  KEY idx_watch_session_user_video (user_id, video_id, started_at),
  CONSTRAINT fk_watch_session_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_watch_session_video FOREIGN KEY (video_id) REFERENCES videos(id),
  CONSTRAINT chk_watch_session_status CHECK (status IN ('ACTIVE','CLOSED','ABNORMAL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE video_watch_ticks (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  video_id BIGINT UNSIGNED NOT NULL,
  session_id BIGINT UNSIGNED NOT NULL,
  tick_no INT UNSIGNED NOT NULL,
  tick_seconds TINYINT UNSIGNED NOT NULL DEFAULT 5,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_watch_tick (user_id, video_id, tick_no),
  KEY idx_watch_tick_session (session_id),
  CONSTRAINT fk_watch_tick_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_watch_tick_video FOREIGN KEY (video_id) REFERENCES videos(id),
  CONSTRAINT fk_watch_tick_session FOREIGN KEY (session_id) REFERENCES video_watch_sessions(id),
  CONSTRAINT chk_watch_tick_seconds CHECK (tick_seconds = 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE user_video_progress (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  video_id BIGINT UNSIGNED NOT NULL,
  valid_seconds INT UNSIGNED NOT NULL DEFAULT 0,
  rewarded_points INT UNSIGNED NOT NULL DEFAULT 0,
  last_watched_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_user_video_progress (user_id, video_id),
  CONSTRAINT fk_video_progress_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_video_progress_video FOREIGN KEY (video_id) REFERENCES videos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE point_accounts (
  user_id BIGINT UNSIGNED PRIMARY KEY,
  balance BIGINT UNSIGNED NOT NULL DEFAULT 0,
  version INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_point_account_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE point_transactions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  amount BIGINT NOT NULL,
  balance_after BIGINT UNSIGNED NOT NULL,
  transaction_type VARCHAR(30) NOT NULL,
  business_type VARCHAR(30) NOT NULL,
  business_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(100) NOT NULL,
  description VARCHAR(255) NOT NULL,
  operator_staff_id BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_point_idempotency (idempotency_key),
  KEY idx_point_user_time (user_id, created_at),
  KEY idx_point_business (business_type, business_id),
  CONSTRAINT fk_point_transaction_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_point_transaction_staff FOREIGN KEY (operator_staff_id) REFERENCES staff_users(id),
  CONSTRAINT chk_point_amount CHECK (amount <> 0),
  CONSTRAINT chk_point_type CHECK (transaction_type IN ('VIDEO_REWARD','INVITE_REWARD','PICKUP_DEBIT','PICKUP_REFUND','MANUAL_ADJUST'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE gifts (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  description TEXT NULL,
  image_url VARCHAR(500) NOT NULL,
  points_cost BIGINT UNSIGNED NOT NULL,
  per_user_limit INT UNSIGNED NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT chk_gift_points CHECK (points_cost > 0),
  CONSTRAINT chk_gift_limit CHECK (per_user_limit IS NULL OR per_user_limit > 0),
  CONSTRAINT chk_gift_status CHECK (status IN ('DRAFT','ACTIVE','INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE store_gift_inventory (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  store_id BIGINT UNSIGNED NOT NULL,
  gift_id BIGINT UNSIGNED NOT NULL,
  available_quantity INT UNSIGNED NOT NULL DEFAULT 0,
  locked_quantity INT UNSIGNED NOT NULL DEFAULT 0,
  redeemed_quantity INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_store_gift (store_id, gift_id),
  CONSTRAINT fk_inventory_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_inventory_gift FOREIGN KEY (gift_id) REFERENCES gifts(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pickup_requests (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  request_no VARCHAR(32) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  gift_id BIGINT UNSIGNED NOT NULL,
  store_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL DEFAULT 1,
  points_cost BIGINT UNSIGNED NOT NULL,
  contact_name VARCHAR(100) NOT NULL,
  contact_mobile VARCHAR(32) NOT NULL,
  voucher_code VARCHAR(64) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'WAIT_CONTACT',
  expires_at TIMESTAMP NOT NULL,
  contacted_at TIMESTAMP NULL,
  picked_up_at TIMESTAMP NULL,
  cancelled_at TIMESTAMP NULL,
  cancel_reason VARCHAR(255) NULL,
  debit_transaction_id BIGINT UNSIGNED NOT NULL,
  refund_transaction_id BIGINT UNSIGNED NULL,
  verified_by BIGINT UNSIGNED NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_pickup_request_no (request_no),
  UNIQUE KEY uk_pickup_voucher (voucher_code),
  KEY idx_pickup_user_time (user_id, created_at),
  KEY idx_pickup_store_status (store_id, status, created_at),
  CONSTRAINT fk_pickup_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_pickup_gift FOREIGN KEY (gift_id) REFERENCES gifts(id),
  CONSTRAINT fk_pickup_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_pickup_debit FOREIGN KEY (debit_transaction_id) REFERENCES point_transactions(id),
  CONSTRAINT fk_pickup_refund FOREIGN KEY (refund_transaction_id) REFERENCES point_transactions(id),
  CONSTRAINT fk_pickup_verifier FOREIGN KEY (verified_by) REFERENCES staff_users(id),
  CONSTRAINT chk_pickup_quantity CHECK (quantity > 0),
  CONSTRAINT chk_pickup_points CHECK (points_cost > 0),
  CONSTRAINT chk_pickup_status CHECK (status IN ('WAIT_CONTACT','WAIT_ARRIVAL','PICKED_UP','CANCELLED','REJECTED','EXPIRED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pickup_status_logs (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  pickup_request_id BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(30) NULL,
  to_status VARCHAR(30) NOT NULL,
  operator_type VARCHAR(20) NOT NULL,
  operator_id BIGINT UNSIGNED NOT NULL,
  remark VARCHAR(255) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_pickup_log_request_time (pickup_request_id, created_at),
  CONSTRAINT fk_pickup_log_request FOREIGN KEY (pickup_request_id) REFERENCES pickup_requests(id),
  CONSTRAINT chk_pickup_operator_type CHECK (operator_type IN ('USER','STAFF','SYSTEM'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE store_leads (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  store_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  pickup_request_id BIGINT UNSIGNED NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'NEW',
  assigned_staff_id BIGINT UNSIGNED NULL,
  last_contact_at TIMESTAMP NULL,
  next_follow_up_at TIMESTAMP NULL,
  remark VARCHAR(500) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_lead_pickup (pickup_request_id),
  KEY idx_lead_store_status (store_id, status),
  CONSTRAINT fk_lead_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_lead_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_lead_pickup FOREIGN KEY (pickup_request_id) REFERENCES pickup_requests(id),
  CONSTRAINT fk_lead_staff FOREIGN KEY (assigned_staff_id) REFERENCES staff_users(id),
  CONSTRAINT chk_lead_status CHECK (status IN ('NEW','CONTACTED','ARRIVED','CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE invitations (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  inviter_user_id BIGINT UNSIGNED NOT NULL,
  invitee_user_id BIGINT UNSIGNED NOT NULL,
  invite_code VARCHAR(32) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'BOUND',
  source_type VARCHAR(30) NOT NULL DEFAULT 'SHARE_LINK',
  source_value VARCHAR(255) NOT NULL DEFAULT '',
  landing_scene VARCHAR(100) NOT NULL DEFAULT '',
  qualified_at TIMESTAMP NULL,
  rewarded_at TIMESTAMP NULL,
  inviter_transaction_id BIGINT UNSIGNED NULL,
  invitee_transaction_id BIGINT UNSIGNED NULL,
  invalid_reason VARCHAR(255) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_invitation_invitee (invitee_user_id),
  KEY idx_invitation_inviter (inviter_user_id, created_at),
  CONSTRAINT fk_invitation_inviter FOREIGN KEY (inviter_user_id) REFERENCES users(id),
  CONSTRAINT fk_invitation_invitee FOREIGN KEY (invitee_user_id) REFERENCES users(id),
  CONSTRAINT fk_invitation_inviter_tx FOREIGN KEY (inviter_transaction_id) REFERENCES point_transactions(id),
  CONSTRAINT fk_invitation_invitee_tx FOREIGN KEY (invitee_transaction_id) REFERENCES point_transactions(id),
  CONSTRAINT chk_invitation_not_self CHECK (inviter_user_id <> invitee_user_id),
  CONSTRAINT chk_invitation_status CHECK (status IN ('BOUND','QUALIFIED','REWARDED','INVALID'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE system_configs (
  config_key VARCHAR(100) PRIMARY KEY,
  config_value JSON NOT NULL,
  description VARCHAR(255) NOT NULL,
  updated_by BIGINT UNSIGNED NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_system_config_staff FOREIGN KEY (updated_by) REFERENCES staff_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE admin_operation_logs (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  staff_id BIGINT UNSIGNED NOT NULL,
  action VARCHAR(80) NOT NULL,
  target_type VARCHAR(50) NOT NULL,
  target_id VARCHAR(64) NOT NULL,
  request_id VARCHAR(64) NOT NULL,
  before_data JSON NULL,
  after_data JSON NULL,
  ip_address VARCHAR(64) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_admin_log_staff_time (staff_id, created_at),
  KEY idx_admin_log_target (target_type, target_id),
  CONSTRAINT fk_admin_log_staff FOREIGN KEY (staff_id) REFERENCES staff_users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE idempotency_records (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  actor_type VARCHAR(20) NOT NULL,
  actor_id BIGINT UNSIGNED NOT NULL,
  operation VARCHAR(100) NOT NULL,
  idempotency_key VARCHAR(100) NOT NULL,
  request_hash CHAR(64) NOT NULL,
  response_status SMALLINT UNSIGNED NULL,
  response_body JSON NULL,
  processing_status VARCHAR(20) NOT NULL DEFAULT 'PROCESSING',
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_idempotency_actor_operation_key (actor_type, actor_id, operation, idempotency_key),
  KEY idx_idempotency_expiry (expires_at),
  CONSTRAINT chk_idempotency_actor_type CHECK (actor_type IN ('USER','STAFF','SYSTEM')),
  CONSTRAINT chk_idempotency_status CHECK (processing_status IN ('PROCESSING','COMPLETED','FAILED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wechat_openid TEXT UNIQUE,
  name TEXT NOT NULL,
  mobile TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
  invite_code TEXT NOT NULL UNIQUE,
  points INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  cover_url TEXT NOT NULL DEFAULT '/assets/video-conference.svg',
  media_url TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Health Knowledge',
  tags TEXT NOT NULL DEFAULT '',
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
  total_points INTEGER NOT NULL DEFAULT 1 CHECK (total_points >= 0),
  status TEXT NOT NULL DEFAULT 'PUBLISHED' CHECK (status IN ('DRAFT', 'PUBLISHED', 'OFFLINE'))
);

CREATE TABLE IF NOT EXISTS watch_sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  video_id INTEGER NOT NULL REFERENCES videos(id),
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  last_position_seconds REAL NOT NULL DEFAULT 0,
  counted_view INTEGER NOT NULL DEFAULT 0 CHECK (counted_view IN (0, 1)),
  last_heartbeat_at REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watch_ticks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  video_id INTEGER NOT NULL REFERENCES videos(id),
  tick_no INTEGER NOT NULL CHECK (tick_no > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, video_id, tick_no)
);

CREATE TABLE IF NOT EXISTS watch_progress (
  user_id INTEGER NOT NULL REFERENCES users(id),
  video_id INTEGER NOT NULL REFERENCES videos(id),
  valid_seconds INTEGER NOT NULL DEFAULT 0,
  rewarded_points INTEGER NOT NULL DEFAULT 0 CHECK (rewarded_points >= 0),
  last_watched_at TEXT,
  PRIMARY KEY (user_id, video_id)
);

CREATE TABLE IF NOT EXISTS point_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  amount INTEGER NOT NULL CHECK (amount <> 0),
  balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
  transaction_type TEXT NOT NULL,
  business_key TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  contact_phone TEXT NOT NULL,
  business_hours TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS gifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  market_price INTEGER NOT NULL DEFAULT 0,
  image_key TEXT NOT NULL DEFAULT 'gift',
  image_url TEXT NOT NULL DEFAULT '',
  image_urls TEXT NOT NULL DEFAULT '[]',
  points_cost INTEGER NOT NULL CHECK (points_cost > 0),
  status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS store_gift_inventory (
  store_id INTEGER NOT NULL REFERENCES stores(id),
  gift_id INTEGER NOT NULL REFERENCES gifts(id),
  available_quantity INTEGER NOT NULL DEFAULT 0 CHECK (available_quantity >= 0),
  locked_quantity INTEGER NOT NULL DEFAULT 0 CHECK (locked_quantity >= 0),
  redeemed_quantity INTEGER NOT NULL DEFAULT 0 CHECK (redeemed_quantity >= 0),
  PRIMARY KEY (store_id, gift_id)
);

CREATE TABLE IF NOT EXISTS pickup_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_no TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  gift_id INTEGER NOT NULL REFERENCES gifts(id),
  store_id INTEGER NOT NULL REFERENCES stores(id),
  contact_name TEXT NOT NULL,
  contact_mobile TEXT NOT NULL,
  points_cost INTEGER NOT NULL CHECK (points_cost > 0),
  voucher_code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'WAIT_CONTACT',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pickup_status_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pickup_id INTEGER NOT NULL REFERENCES pickup_requests(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  operator_type TEXT NOT NULL,
  remark TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS store_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pickup_id INTEGER NOT NULL UNIQUE REFERENCES pickup_requests(id),
  store_id INTEGER NOT NULL REFERENCES stores(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'NEW',
  remark TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invitations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inviter_user_id INTEGER NOT NULL REFERENCES users(id),
  invitee_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'BOUND',
  source_type TEXT NOT NULL DEFAULT 'SHARE_LINK',
  source_value TEXT NOT NULL DEFAULT '',
  landing_scene TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  rewarded_at TEXT,
  CHECK (inviter_user_id <> invitee_user_id)
);

CREATE TABLE IF NOT EXISTS idempotency_records (
  actor_type TEXT NOT NULL,
  actor_id INTEGER NOT NULL,
  operation TEXT NOT NULL,
  request_key TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (actor_type, actor_id, operation, request_key)
);

CREATE TABLE IF NOT EXISTS staff_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('ADMIN', 'STORE_VERIFIER')),
  store_id INTEGER REFERENCES stores(id),
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staff_sessions (
  token_hash TEXT PRIMARY KEY,
  staff_id INTEGER NOT NULL REFERENCES staff_users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_configs (
  config_key TEXT PRIMARY KEY,
  config_value TEXT NOT NULL,
  description TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_assets (
  storage_ref TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('VIDEO', 'IMAGE')),
  original_name TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'UPLOAD_PENDING' CHECK (
    status IN ('UPLOAD_PENDING', 'UNBOUND', 'BOUND', 'PENDING_DELETE', 'READY_DELETE', 'MISSING', 'DELETED')
  ),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_checked_at TEXT,
  cleanup_requested_at TEXT,
  delete_after TEXT,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS admin_operation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  staff_id INTEGER NOT NULL REFERENCES staff_users(id),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game_services (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, server_id TEXT NOT NULL,
 online_source TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}',
 sampled_at INTEGER NOT NULL DEFAULT 0, received_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_history (
 game_id TEXT NOT NULL, sampled_at INTEGER NOT NULL, online INTEGER,
 status TEXT NOT NULL, PRIMARY KEY(game_id, sampled_at)
);

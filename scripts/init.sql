-- init.sql: Database initialization for local development
-- Loaded automatically by MySQL Docker container on first start

CREATE DATABASE IF NOT EXISTS app CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE app;

-- Seed tables are created by Alembic migrations.
-- This file is for any MySQL-specific setup that Alembic doesn't handle.

-- Grant permissions
GRANT ALL PRIVILEGES ON app.* TO 'app'@'%';
FLUSH PRIVILEGES;

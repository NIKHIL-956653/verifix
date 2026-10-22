-- Runs once, on the first start of an empty data volume.
-- The test suite migrates and wipes this database; the dev database is never touched by tests.
CREATE DATABASE verifix_test OWNER verifix;
